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

# Dependencies first for layer caching.
COPY pipeline/gold_refresh.requirements.txt /app/pipeline/gold_refresh.requirements.txt
RUN pip install --no-cache-dir -r /app/pipeline/gold_refresh.requirements.txt

# The whole repo — the orchestrator shells out to the silver/gold/dbt entrypoints,
# and the model + great_expectations packages are imported at runtime.
COPY . /app

# Pull dbt packages (dbt_external_tables, dbt_utils) at build time so the run is offline-deps.
RUN cd /app/dbt && dbt deps

ENTRYPOINT ["python", "pipeline/gold_refresh.py"]
