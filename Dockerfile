# Curb Energy Monitor — Community Root Access Server (containerized)
#
# Packages payload/serve.py into a small, non-root container. The server
# impersonates updates.energycurb.com so that abandoned Curb Energy Monitor
# devices (bricked by Curb Inc.'s February 2026 cloud shutdown) can be
# recovered by their owners. See docs/DOCKER.md for the full workflow,
# including the DNS redirect this still requires.

FROM python:3.12-slim

# openssl is used at runtime to generate the server's self-signed TLS cert.
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/*

# Run as a non-root user. serve.py listens on 80/443 by default, which
# requires root — instead we run it on unprivileged ports (8080/8443)
# inside the container and let Docker's port mapping present them to the
# LAN as 80/443 (see docker-compose.yml). This keeps the container from
# ever needing root, even though the payload it serves grants root on the
# Curb device itself.
RUN useradd --uid 1000 --create-home --home-dir /app --shell /bin/false curb

WORKDIR /app

# Payload files (server + the pre-built encrypted root-access payload)
COPY --chown=curb:curb payload/ /app/

# Runtime state (webroot cache + generated TLS cert/key) lives here,
# separate from the read-only app files, so it can be put on a volume.
RUN mkdir -p /data && chown curb:curb /data
VOLUME ["/data"]

ENV CURB_DATA_DIR=/data \
    CURB_HTTP_PORT=8080 \
    CURB_HTTPS_PORT=8443 \
    PYTHONUNBUFFERED=1

EXPOSE 8080 8443

USER curb

ENTRYPOINT ["python3", "serve.py"]
