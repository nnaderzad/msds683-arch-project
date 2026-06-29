# G1 — gold-refresh Cloud Run Job image.
# Runs pipeline/gold_refresh.py: silver (A1/A2/A3) -> dbt build -> forecast (D2) -> GX gate.
# Auth is ADC: the job's Cloud Run service account supplies credentials at runtime
# (dbt profiles.yml uses method: oauth -> ADC; the BQ clients use ADC too). No keys baked in.
#
# Build context is the repo root so the whole project is available:
#   docker build -f pipeline/gold_refresh.Dockerfile -t <image> .
# (Terraform builds + pushes this to Artifact Registry via Cloud Build — see
#  terraform/gold_refresh_job.tf.)

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DBT_PROFILES_DIR=/app/dbt

WORKDIR /app

# The silver transforms (A1/A2/A3) shell out to `gsutil ls`/`gsutil cat` to read bronze
# JSON from GCS, so the image needs the Google Cloud CLI. On Cloud Run it auto-authenticates
# via the metadata server (the job's service account) — no config, no keys.
# (Follow-up: migrate those scripts to the google-cloud-storage client, as
#  pipeline/silver/trends_series_to_silver.py already does, and drop this.)
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
    && curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
       | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
       > /etc/apt/sources.list.d/google-cloud-sdk.list \
    && apt-get update && apt-get install -y --no-install-recommends google-cloud-cli \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first for layer caching.
COPY pipeline/gold_refresh.requirements.txt /app/pipeline/gold_refresh.requirements.txt
RUN pip install --no-cache-dir -r /app/pipeline/gold_refresh.requirements.txt

# The whole repo — the orchestrator shells out to the silver/gold/dbt entrypoints,
# and the model + great_expectations packages are imported at runtime.
COPY . /app

# Pull dbt packages (dbt_external_tables, dbt_utils) at build time so the run is offline-deps.
RUN cd /app/dbt && dbt deps

ENTRYPOINT ["python", "pipeline/gold_refresh.py"]
