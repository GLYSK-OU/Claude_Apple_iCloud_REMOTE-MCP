FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

# Never root. A container holding a live iCloud session should not be able to
# write outside its own volume, and a host bind-mount has to be owned by a uid
# the container actually runs as — mismatched ownership is a silent start-up
# failure that looks like a config bug.
RUN groupadd --system --gid 10001 icloud \
    && useradd --system --uid 10001 --gid 10001 --home /home/icloud --create-home icloud

# The Apple session and OAuth store live here. Mount a volume, or every
# restart needs a fresh two-factor code and reconnecting the Claude connector.
RUN mkdir -p /data && chown -R 10001:10001 /data
VOLUME ["/data"]

USER 10001:10001

ENV ICLOUD_SESSION_DIR=/data/icloud-session \
    OAUTH_STORE_PATH=/data/oauth-store.json \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

ENTRYPOINT ["icloud-drive-mcp"]
CMD ["http"]
