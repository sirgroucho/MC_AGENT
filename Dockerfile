FROM python:3.12-slim

# non-root
RUN useradd -m -u 10001 agent

# tiny base + certs + init
RUN apt-get update && apt-get install -y --no-install-recommends \
    tini ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# deps: psutil (metrics), requests (HTTP), mcstatus (Query)
RUN pip install --no-cache-dir psutil requests mcstatus

WORKDIR /app
COPY agent.py /app/agent.py
RUN chown -R agent:agent /app

USER agent
ENV PYTHONUNBUFFERED=1
VOLUME ["/app/queue"]

ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["python","/app/agent.py"]
