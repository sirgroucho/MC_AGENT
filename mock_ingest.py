from flask import Flask, request

app = Flask(__name__)

@app.post("/api/ingest")
def ingest():
    print("\n--- Incoming Event ---")
    print("Headers:", dict(request.headers))
    print("Body:", request.get_data(as_text=True))
    return {"ok": True}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
