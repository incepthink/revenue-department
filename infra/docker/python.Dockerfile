# Shared image for services/api and services/worker.
FROM python:3.12-slim

# Tesseract (Marathi + English) is an optional, fully offline OCR engine used for
# synthetic docs and the OCR benchmark. Real dockets use Azure Document Intelligence.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-mar tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY services/api/pyproject.toml services/api/pyproject.toml
COPY services/api/src services/api/src
RUN pip install --no-cache-dir -e "services/api[tesseract]"

COPY config config
COPY packages/schemas packages/schemas
COPY services services
ENV RCI_REPO_ROOT=/app PYTHONUNBUFFERED=1
WORKDIR /app/services/api
