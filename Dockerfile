# ── Build Stage (full image with build tools) ──
FROM python:3.9 AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# ── Runtime Stage (lightweight slim image) ──
FROM python:3.9-slim

WORKDIR /app

COPY --from=builder /app /app

CMD ["python", "data_analysis.py"]