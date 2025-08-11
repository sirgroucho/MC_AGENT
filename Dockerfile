FROM python:3.12-slim

# Add a non-root user
RUN useradd -m -u 10001 agent

# Minimal deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    tini ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Python deps
RUN pip install --no-cache-dir psutil requests

WORKDIR /app
COPY agent.py /app/agent.py
RUN chown -R agent:agent /app

USER agent

# Read-only FS by default; create writable dirs
VOLUME ["/app/queue"]  # offline queue spool
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["python","/app/agent.py"]
