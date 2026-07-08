#!/usr/bin/env bash
# Deploy the Ticketmaster daily-extract Cloud Function (gen2) from THIS directory.
# Mirrors the terraform config in terraform/ticketmaster_scheduler.tf — use this
# when the terraform state isn't on your machine; the next `terraform apply`
# (Niki's machine) reconciles harmlessly since the source is identical.
#
# Usage:  bash cloud_functions/ticketmaster_daily/deploy.sh   (from repo root)
#         bash deploy.sh                                      (from this dir)
set -euo pipefail
cd "$(dirname "$0")"

gcloud functions deploy ticketmaster-daily-extract \
  --project data-architecture-498123 \
  --region us-west1 \
  --gen2 \
  --runtime python312 \
  --entry-point run \
  --trigger-http \
  --no-allow-unauthenticated \
  --memory 1Gi \
  --timeout 1800s \
  --max-instances 1 \
  --service-account ticketmaster-extract@data-architecture-498123.iam.gserviceaccount.com \
  --set-env-vars "BQ_DATASET=event_demand_analytics,CLASSIFICATION_NAME=music,DAYS_AHEAD=180,GCP_PROJECT=data-architecture-498123,GCS_PROCESSED_BUCKET=data-architecture-498123-processed,GCS_RAW_BUCKET=data-architecture-498123-raw,LOG_EXECUTION_ID=true,MAX_CALLS_PER_RUN=780,MAX_PAGES=5,PAGE_SIZE=200,SLICE_DAYS=14,STATE_CODES=ALL" \
  --set-secrets "TICKETMASTER_API_KEY=ticketmaster-api-key:latest" \
  --source .

echo
echo "Deployed. Verify the build timestamp is NOW:"
gcloud functions describe ticketmaster-daily-extract --region us-west1 \
  --project data-architecture-498123 --format="value(updateTime)"
