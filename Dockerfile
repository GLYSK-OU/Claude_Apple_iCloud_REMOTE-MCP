FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

# The Apple session and OAuth store live here. Mount a volume, or every
# restart needs a fresh two-factor code and reconnecting the Claude connector.
RUN mkdir -p /data
VOLUME ["/data"]

ENV ICLOUD_SESSION_DIR=/data/icloud-session \
    OAUTH_STORE_PATH=/data/oauth-store.json \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

ENTRYPOINT ["icloud-drive-mcp"]
CMD ["http"]
