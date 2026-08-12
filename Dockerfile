# ── Build Stage (full image with build tools) ──
FROM python:3.9 AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# ── Runtime Stage (lightweight slim image) ──
FROM python:3.9-slim

WORKDIR /app

# Installed packages live in site-packages, not /app — copy them across as well,
# otherwise the runtime image has the source code but none of its dependencies.
COPY --from=builder /usr/local/lib/python3.9/site-packages /usr/local/lib/python3.9/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app /app

CMD ["python", "data_analysis.py"]