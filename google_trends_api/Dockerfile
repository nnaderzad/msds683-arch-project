# Container for the Google Trends ingestion Cloud Run Jobs (job.py).
# Build context is the REPO ROOT — the image needs both common/ and
# google_trends_api/:
#   gcloud builds submit --config google_trends_api/cloudbuild.yaml .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app:/app/google_trends_api \
    GCP_PROJECT=data-architecture-498123

WORKDIR /app

COPY google_trends_api/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# Shared GCS helper + the Trends package (code + committed reference tables).
COPY common/ /app/common/
COPY google_trends_api/ /app/google_trends_api/

# Cloud Run Jobs inject CLOUD_RUN_TASK_INDEX / CLOUD_RUN_TASK_COUNT; job.py
# shards the work queue on them. JOB_MODE etc. come from the job's env (Terraform).
ENTRYPOINT ["python", "/app/google_trends_api/job.py"]
