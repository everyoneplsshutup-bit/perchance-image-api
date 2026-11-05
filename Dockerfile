# Render (free) deploy for the Perchance Image API.
# Build with: a repo containing this Dockerfile + requirements.txt + app/
# + perchance-test/controls.json (the 79 art styles; no secrets in it).
# NOTE: do NOT copy userkey.txt into the image. The API works without a
# Perchance key (Pollinations fallback serves images); if a working key
# is ever available, set it as a Render env var named USER_KEY instead.
# Render sets $PORT — the server must bind to it.
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
COPY perchance-test/ ./perchance-test/
EXPOSE 10000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
