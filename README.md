# Minecraft Server Agent

**Lightweight, resilient, and secure Docker agent that tails a Minecraft server’s logs to stream live events and metrics to your cloud dashboard.**

---

##  Table of Contents

- [Purpose](#purpose)  
- [Features](#features)  
- [Getting Started](#getting-started)  
- [Configuration](#configuration)  
- [Usage](#usage)  
- [Dry Run Mode](#dry-run-mode)  
- [Agent Behavior](#agent-behavior)  
- [Offline Queue & Reliability](#offline-queue--reliability)  
- [Security Considerations](#security-considerations)  
- [Repository Structure](#repository-structure)  
- [Contributing](#contributing)  
- [License](#license)  

---

##  Purpose

This agent runs alongside a Dockerized Minecraft server (like `itzg/minecraft-server`), reading its logs to detect:
- Player joins/leaves (with real-time count)
- Server start/stop/crash events
- Periodic system metrics (CPU, memory, load) **only when players are online**

All events are **HMAC-signed** and sent to a configured ingest endpoint—ideal for feeding live dashboards.

---

##  Features

- **Log tailing** including rotation-safe detection
- **Reliable event queuing** with on-disk persistence
- **Graceful handling of restarts and SIGTERM**
- **Configurable interval for metric dispatch**
- **Supports dry-run mode** before cloud setup
- **Minimal base image** and **non-root execution** for safety

---

##  Getting Started

1. Clone the repo:
   ```bash
   git clone https://github.com/your-org/minecraft-agent.git
   cd minecraft-agent
````

2. Copy and update the `.env.example`:

   ```bash
   cp .env.example .env
   # Fill in SERVER_ID, AGENT_KEY, INGEST_URL when cloud is ready
   ```

3. Build and start via Docker Compose:

   ```bash
   docker compose up -d --build
   ```

   * Initially runs in **dry-run** (prints payloads). Later, set `DRY_RUN=0` when backend is live.

---

## Configuration

Set values in `.env` or override in your deployment environment:

| Variable          | Description                                            | Required                          |
| ----------------- | ------------------------------------------------------ | --------------------------------- |
| `SERVER_ID`       | Unique identifier for your server                      |                                   |
| `AGENT_KEY`       | HMAC secret key                                        |                                   |
| `INGEST_URL`      | Cloud ingest API endpoint (use Tailscale IP ideally)   | –                                 |
| `MC_LOG`          | Path to `latest.log` inside container                  | defaults to `/mc/logs/latest.log` |
| `METRIC_INTERVAL` | Interval in seconds between metric sends               | default `30`                      |
| `QUEUE_DIR`       | Directory for offline queue storage                    | default `/app/queue`              |
| `DRY_RUN`         | Set `1` to print payloads instead of sending over HTTP | default `1`                       |

---

## Usage

* **Add agent** to your existing `docker-compose.yml`, mounting the Minecraft logs (e.g., `mcdata/logs`).
* The agent will start alongside your Minecraft server, tail logs, and emit events.
* Once the cloud endpoint is ready, switch off `DRY_RUN`, and real traffic begins flowing.

---

## Dry Run Mode

By default (`DRY_RUN=1`), the agent will **print JSON payloads** instead of sending them.

```json
[DRY_RUN] {"server_id":"beelink-01","event":"player_joined","player":"Steve","players_online":3,"ts":1723400000}
```

Perfect for validating events before integrating with a backend.

---

## Agent Behavior

* **`server_started`** emitted on container start
* **`player_joined` / `player_left`** as users connect/disconnect
* **`metrics`** sent every `METRIC_INTERVAL`, only when players are online
* **`server_stopped`** on clean shutdown, and **`server_crashed`** on abrupt failure

---

## Offline Queue & Reliability

If the backend is unreachable, events are queued on disk (`QUEUE_DIR`) and automatically retried with exponential backoff until successful delivery. Ideal for handling network glitches or cloud uptime issues.

---

## Security Considerations

* Runs as **non-root user** with **read-only filesystem** and `no-new-privileges`
* Requires **HMAC signing** for payload integrity
* Recommended to connect over **Tailscale** and firewall ingress to `tailscale0` only
* Keep `.env` out of Git (see `.gitignore`), and rotate `AGENT_KEY` per server

---

## Repository Structure

```
/
├── agent.py            # Core agent logic (log tailing, event generation, queuing)
├── Dockerfile          # Lightweight, secure build
├── docker-compose.yml  # Example for Minecraft + agent deployment
├── .env.example        # Template for all required configuration
├── .gitignore          
├── .dockerignore       
└── README.md           # This file
```

---

## Contributing

Contributions are welcome! Whether it's better log parsing (for mods/pack variants) or new metrics, just open an issue or PR. Please follow standard GitHub flow and sign-off commits if required.

---

## License

[MIT License](LICENSE) — feel free to use, modify, and redistribute with attribution.

---
