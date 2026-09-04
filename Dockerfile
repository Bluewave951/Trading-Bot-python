FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Phase 1: smoke-tests the data pipeline.
# Later phases should switch this to `uvicorn src.web.app:app --host 0.0.0.0 --port 8000`
# once the scheduler + FastAPI app exist.
CMD ["python", "main.py"]
