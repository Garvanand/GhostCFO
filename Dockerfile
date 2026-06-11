# GhostCFO Dockerfile
# Based on Python 3.11 slim

FROM python:3.11-slim-bookworm

# System dependencies for PyMuPDF, cryptography, and PostgreSQL client
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    mupdf \
    mupdf-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY ghostcfo/ /app/ghostcfo/
COPY prompts/ /app/prompts/

# Expose FastAPI port
EXPOSE 8001

# Run server
CMD ["python", "-m", "uvicorn", "ghostcfo.server.app:app", "--host", "0.0.0.0", "--port", "8001"]
