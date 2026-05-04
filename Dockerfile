FROM python:3.11-slim

# System packages: only what pypdf and the Google libs need at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for layer caching.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy application source.
COPY . /app

# Railway provides $PORT at runtime. EXPOSE is informational.
EXPOSE 8080

# scripts/start.py runs migrations idempotently then exec's gunicorn.
CMD ["python", "scripts/start.py"]
