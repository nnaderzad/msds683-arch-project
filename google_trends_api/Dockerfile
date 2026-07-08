# Container for the project's ingestion Cloud Run Jobs:
#   - Google Trends  (google_trends_api/job.py)         [default ENTRYPOINT]
#   - YouTube        (youtube_api/collect_youtube.py)   [via command override]
#   - 19hz + ticket pages (nineteenhz_api/job.py)       [via command override]
#   - RA             (ra_api/collect_ra.py)             [via command override]
# Build context is the REPO ROOT — the image needs common/ plus every source dir:
#   gcloud builds submit --config google_trends_api/cloudbuild.yaml .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app:/app/google_trends_api:/app/youtube_api \
    GCP_PROJECT=data-architecture-498123

WORKDIR /app

COPY google_trends_api/requirements.txt /app/req-gtrends.txt
COPY youtube_api/requirements.txt /app/req-youtube.txt
RUN pip install -r /app/req-gtrends.txt -r /app/req-youtube.txt

# Shared GCS helper + the source packages (code + committed reference tables).
# 19hz/RA collectors are stdlib-only (their --land-raw path uses common/gcs_io,
# whose google-cloud-storage dep the gtrends requirements already install).
COPY common/ /app/common/
COPY google_trends_api/ /app/google_trends_api/
COPY youtube_api/ /app/youtube_api/
COPY nineteenhz_api/ /app/nineteenhz_api/
COPY ra_api/ /app/ra_api/

# Default = the Trends job (shards on CLOUD_RUN_TASK_INDEX/COUNT); the YouTube job
# overrides the container command to run collect_youtube.py. Env comes from the job.
ENTRYPOINT ["python", "/app/google_trends_api/job.py"]
