FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (excludes .env, .venv, __pycache__ via .dockerignore)
COPY . .

# Expose API port
EXPOSE 8000

# Default: run the FastAPI server so the pipeline can be triggered via HTTP.
# Override with CMD ["python", "run_pipeline.py"] to run the pipeline directly.
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
