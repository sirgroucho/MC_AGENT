import os, re, time, json, hmac, hashlib, socket, signal, threading
from datetime import datetime, timezone
import requests, psutil

SERVER_ID   = os.getenv("SERVER_ID", "beelink-01")
AGENT_KEY   = os.getenv("AGENT_KEY", "")
INGEST_URL  = os.getenv("INGEST_URL", "")  # required when DRY_RUN=0
LOG_PATH    = os.getenv("MC_LOG", "/mc/logs/latest.log")
INTERVAL    = int(os.getenv("METRIC_INTERVAL", "30"))
QUEUE_DIR   = os.getenv("QUEUE_DIR", "/app/queue")
DRY_RUN     = os.getenv("DRY_RUN", "1") == "1"

# 1.12.2 typical lines (Forge):
# [HH:MM:SS] [Server thread/INFO]: Steve joined the game
# [HH:MM:SS] [Server thread/INFO]: Steve left the game
JOIN_RX  = re.compile(r"\] \[Server thread/INFO\]: (.+?) joined the game")
LEAVE_RX = re.compile(r"\] \[Server thread/INFO\]: (.+?) left the game")

# Some servers/plugins format differently, try alternates:
JOIN_ALT  = re.compile(r"joined the game:?\s*(.+)?")
LEAVE_ALT = re.compile(r"left the game:?\s*(.+)?")

players_online = 0
stop_event = threading.Event()

def now_ts() -> int:
    return int(time.time())

def iso8601() -> str:
    return datetime.now(timezone.utc).isoformat()

def ensure_dir(p):
    os.makedirs(p, exist_ok=True)

def sign_body(body: bytes, ts: str) -> dict:
    if not AGENT_KEY:
        raise RuntimeError("AGENT_KEY not set")
    mac = hmac.new(AGENT_KEY.encode(), body + ts.encode(), hashlib.sha256).hexdigest()
    return {
        "X-Signature": f"sha256={mac}",
        "X-Timestamp": ts,
        "Content-Type": "application/json",
    }

def metrics_snapshot():
    # psutil.cpu_percent(None) returns % since last call; first call primes it
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory().percent
    load1 = 0.0
    try:
        load1 = os.getloadavg()[0]
    except Exception:
        pass
    return {
        "cpu_pct": cpu,
        "mem_pct": mem,
        "load1": load1,
        "hostname": socket.gethostname(),
        "agent_time": iso8601(),
    }

def payload(event: str, **kw):
    d = {"server_id": SERVER_ID, "event": event, "ts": now_ts()}
    d.update(kw)
    return d

# -------- Offline queue (durable, append-only files) --------
def queue_path(seq: int) -> str:
    return os.path.join(QUEUE_DIR, f"{seq:020d}.json")

def queue_list():
    ensure_dir(QUEUE_DIR)
    files = [f for f in os.listdir(QUEUE_DIR) if f.endswith(".json")]
    files.sort()
    return [os.path.join(QUEUE_DIR, f) for f in files]

def queue_push(obj: dict):
    ensure_dir(QUEUE_DIR)
    # Use monotonic timestamp+counter to avoid collisions
    seq = int(time.time() * 1000)
    p = queue_path(seq)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.replace(tmp, p)

def queue_pop_one():
    files = queue_list()
    if not files:
        return None, None
    p = files[0]
    try:
        with open(p, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return p, obj
    except Exception:
        # Corrupt? drop it
        try: os.remove(p)
        except: pass
        return None, None

def queue_delete(path: str):
    try: os.remove(path)
    except: pass

# -------- Network send with backoff --------
def http_send(obj: dict) -> bool:
    if DRY_RUN:
        print("[DRY_RUN]", json.dumps(obj))
        return True
    if not INGEST_URL:
        print("[WARN] INGEST_URL not set; queuing")
        return False
    body = json.dumps(obj, separators=(",", ":")).encode()
    headers = sign_body(body, str(now_ts()))
    try:
        r = requests.post(INGEST_URL, data=body, headers=headers, timeout=5)
        if r.status_code // 100 == 2:
            return True
        print(f"[WARN] ingest HTTP {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"[WARN] ingest error: {e}")
        return False

def send_or_queue(obj: dict):
    ok = http_send(obj)
    if not ok:
        queue_push(obj)

def queue_drain_loop():
    # Background: retry queued payloads with exponential backoff
    delay = 2
    while not stop_event.wait(delay):
        path, obj = queue_pop_one()
        if obj is None:
            delay = min(30, delay * 2)  # backoff until there is work
            continue
        ok = http_send(obj)
        if ok:
            queue_delete(path)
            delay = 1  # got progress: speed up
        else:
            # Put it back by not deleting; wait a bit longer
            delay = min(10, delay * 2)

# -------- Log tail (rotation-aware) --------
def tail_follow(path):
    """
    Simple rotation handling:
    - if file truncates (size decreases), reopen
    - if inode changes, reopen
    """
    last_inode = None
    f = None
    pos = 0
    while not stop_event.is_set():
        try:
            st = os.stat(path)
            inode = (st.st_ino, st.st_dev)
            if f is None or inode != last_inode:
                if f: f.close()
                f = open(path, "r", encoding="utf-8", errors="ignore")
                last_inode = inode
                pos = 0
                f.seek(0, 2)  # jump to end on first open
            cur = f.tell()
            line = f.readline()
            if not line:
                # Detect truncation
                new_st = os.stat(path)
                if new_st.st_size < cur:
                    f.seek(0)  # file truncated
                else:
                    time.sleep(0.2)
                continue
            yield line
        except FileNotFoundError:
            time.sleep(0.5)
        except Exception as e:
            print(f"[WARN] tail error: {e}")
            time.sleep(0.5)
    if f:
        f.close()

def detect_player_events():
    global players_online
    for line in tail_follow(LOG_PATH):
        m = JOIN_RX.search(line) or JOIN_ALT.search(line)
        if m:
            name = m.group(1).strip() if m.group(1) else "unknown"
            players_online += 1
            send_or_queue(payload("player_joined", player=name, players_online=players_online))
            continue
        m = LEAVE_RX.search(line) or LEAVE_ALT.search(line)
        if m:
            name = m.group(1).strip() if m.group(1) else "unknown"
            players_online = max(players_online - 1, 0)
            send_or_queue(payload("player_left", player=name, players_online=players_online))
            continue

def metrics_loop():
    # Prime CPU percent baseline
    psutil.cpu_percent(interval=None)
    last = 0
    while not stop_event.wait(0.2):
        if players_online <= 0:
            last = 0
            continue
        now = time.time()
        if now - last >= INTERVAL:
            last = now
            send_or_queue(payload("metrics",
                                  metrics=metrics_snapshot(),
                                  players_online=players_online))

def handle_signals():
    def _sigterm(_signo, _frame):
        stop_event.set()
        try:
            send_or_queue(payload("server_stopped"))
        finally:
            # Give queue a moment to flush
            time.sleep(0.5)
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

def main():
    print("[INFO] Agent starting",
          f"server_id={SERVER_ID}",
          f"log={LOG_PATH}",
          f"interval={INTERVAL}s",
          f"dry_run={DRY_RUN}",
          sep=" | ")
    ensure_dir(QUEUE_DIR)
    handle_signals()

    # Announce boot (optional)
    try:
        send_or_queue(payload("server_started"))
    except Exception as e:
        print(f"[WARN] start event failed: {e}")

    # Start queue drain thread
    t_q = threading.Thread(target=queue_drain_loop, daemon=True)
    t_q.start()

    # Start metrics thread
    t_m = threading.Thread(target=metrics_loop, daemon=True)
    t_m.start()

    # Foreground: parse logs
    try:
        detect_player_events()
    finally:
        stop_event.set()

if __name__ == "__main__":
    main()
