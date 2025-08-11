import os, time, json, hmac, hashlib, socket, signal, threading
from datetime import datetime, timezone
import requests, psutil

# Optional: Query mode
from mcstatus import JavaServer

# ------- Config -------
SERVER_ID    = os.getenv("SERVER_ID", "beelink-01")
AGENT_KEY    = os.getenv("AGENT_KEY", "")
INGEST_URL   = os.getenv("INGEST_URL", "")
INTERVAL     = int(os.getenv("METRIC_INTERVAL", "30"))
QUEUE_DIR    = os.getenv("QUEUE_DIR", "/app/queue")
DRY_RUN      = os.getenv("DRY_RUN", "1") == "1"

DETECT_MODE  = os.getenv("DETECT_MODE", "query")  # "query" (recommended) or "log"
MC_HOST      = os.getenv("MC_HOST", "mc")         # service name or 127.0.0.1
MC_QUERY_PORT= int(os.getenv("MC_QUERY_PORT", "25565"))

# (log mode vars kept for compatibility; not used in query mode)
MC_LOG       = os.getenv("MC_LOG", "/mc/logs/latest.log")

# ------- State -------
stop_event = threading.Event()
players_online = 0  # only used in log mode

# ------- Helpers -------
def now_ts() -> int: return int(time.time())
def iso8601() -> str: return datetime.now(timezone.utc).isoformat()

def metrics_snapshot():
    psutil.cpu_percent(interval=None)  # prime
    return {
        "cpu_pct": psutil.cpu_percent(interval=None),
        "mem_pct": psutil.virtual_memory().percent,
        "load1": os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0,
        "hostname": socket.gethostname(),
        "agent_time": iso8601(),
    }

def payload(event: str, **kw):
    d = {"server_id": SERVER_ID, "event": event, "ts": now_ts()}
    d.update(kw); return d

# ------- Offline queue -------
def ensure_dir(p): os.makedirs(p, exist_ok=True)
def _qpath(seq:int): return os.path.join(QUEUE_DIR, f"{seq:020d}.json")
def queue_push(obj:dict):
    ensure_dir(QUEUE_DIR); p=_qpath(int(time.time()*1000)); tmp=p+".tmp"
    with open(tmp,"w",encoding="utf-8") as f: json.dump(obj,f,separators=(",",":"))
    os.replace(tmp,p)
def queue_pop_one():
    ensure_dir(QUEUE_DIR)
    files=sorted([f for f in os.listdir(QUEUE_DIR) if f.endswith(".json")])
    if not files: return None,None
    p=os.path.join(QUEUE_DIR,files[0])
    try:
        with open(p,"r",encoding="utf-8") as f: obj=json.load(f)
        return p,obj
    except Exception:
        try: os.remove(p)
        except: pass
        return None,None
def queue_delete(p): 
    try: os.remove(p)
    except: pass

# ------- HTTP/HMAC -------
def sign_body(body:bytes, ts:str)->dict:
    if not AGENT_KEY: raise RuntimeError("AGENT_KEY not set")
    mac=hmac.new(AGENT_KEY.encode(), body+ts.encode(), hashlib.sha256).hexdigest()
    return {"X-Signature":f"sha256={mac}", "X-Timestamp":ts, "Content-Type":"application/json"}

def http_send(obj:dict)->bool:
    if DRY_RUN:
        print("[DRY_RUN]", json.dumps(obj)); return True
    if not INGEST_URL:
        print("[WARN] INGEST_URL not set; queuing"); return False
    body=json.dumps(obj,separators=(",",":")).encode()
    headers=sign_body(body,str(now_ts()))
    try:
        r=requests.post(INGEST_URL,data=body,headers=headers,timeout=5)
        if 200 <= r.status_code < 300: return True
        print(f"[WARN] ingest {r.status_code}: {r.text[:200]}"); return False
    except Exception as e:
        print(f"[WARN] ingest error: {e}"); return False

def send_or_queue(obj:dict):
    if not http_send(obj): queue_push(obj)

def queue_drain_loop():
    delay=2
    while not stop_event.wait(delay):
        p,obj=queue_pop_one()
        if obj is None:
            delay=min(30, delay*2); continue
        if http_send(obj):
            queue_delete(p); delay=1
        else:
            delay=min(10, delay*2)

# ------- Query mode -------
def query_players_once():
    try:
        srv = JavaServer(MC_HOST, MC_QUERY_PORT)  # UDP
        resp = srv.query()
        return set(resp.players.list or [])
    except Exception:
        return None

def query_loop():
    known=set()
    last_metrics=0.0
    while not stop_event.wait(1.5):
        names=query_players_once()
        if names is None:
            continue
        # joins
        for n in sorted(names - known):
            send_or_queue(payload("player_joined", player=n, players_online=len(names)))
        # leaves
        for n in sorted(known - names):
            send_or_queue(payload("player_left", player=n, players_online=len(names)))
        # metrics when someone online
        now=time.time()
        if names and (now - last_metrics) >= INTERVAL:
            last_metrics=now
            send_or_queue(payload("metrics", metrics=metrics_snapshot(), players_online=len(names)))
        known=names

# ------- Log mode (kept for completeness) -------
def tail_follow(path):
    last_inode=None; f=None
    while not stop_event.is_set():
        try:
            st=os.stat(path); inode=(st.st_ino, st.st_dev)
            if f is None or inode != last_inode:
                if f: f.close()
                f=open(path,"r",encoding="utf-8",errors="ignore")
                last_inode=inode; f.seek(0,2)
            line=f.readline()
            if not line: time.sleep(0.2); continue
            yield line
        except FileNotFoundError: time.sleep(0.5)
        except Exception as e: print("[WARN] tail error",e); time.sleep(0.5)
    if f: f.close()

import re
JOIN_RX  = re.compile(r"] \[Server thread/INFO\]: (.+?) joined the game")
LEAVE_RX = re.compile(r"] \[Server thread/INFO\]: (.+?) left the game")
def detect_player_events():
    global players_online
    for line in tail_follow(MC_LOG):
        m=JOIN_RX.search(line)
        if m:
            players_online+=1
            send_or_queue(payload("player_joined", player=m.group(1), players_online=players_online)); continue
        m=LEAVE_RX.search(line)
        if m:
            players_online=max(players_online-1,0)
            send_or_queue(payload("player_left", player=m.group(1), players_online=players_online)); continue

def metrics_loop_logmode():
    last=0
    while not stop_event.wait(0.2):
        if players_online<=0: last=0; continue
        now=time.time()
        if now-last>=INTERVAL:
            last=now
            send_or_queue(payload("metrics", metrics=metrics_snapshot(), players_online=players_online))

# ------- Signals & main -------
def handle_signals():
    def _stop(_s,_f):
        stop_event.set()
        try: send_or_queue(payload("server_stopped"))
        finally: time.sleep(0.3)
    signal.signal(signal.SIGTERM,_stop)
    signal.signal(signal.SIGINT,_stop)

def main():
    print(f"[INFO] Agent starting | mode={DETECT_MODE} | host={MC_HOST}:{MC_QUERY_PORT} | dry_run={DRY_RUN}")
    handle_signals()
    os.makedirs(QUEUE_DIR, exist_ok=True)
    try: send_or_queue(payload("server_started"))
    except: pass

    t_q=threading.Thread(target=queue_drain_loop, daemon=True); t_q.start()

    if DETECT_MODE.lower()=="query":
        query_loop()  # foreground loop
    else:
        t_m=threading.Thread(target=metrics_loop_logmode, daemon=True); t_m.start()
        detect_player_events()  # foreground

if __name__=="__main__":
    main()
