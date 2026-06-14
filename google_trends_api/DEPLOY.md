# Deploying the Google Trends ingestion (Cloud Run Jobs + Scheduler)

Everything is Terraform-managed (`terraform/google_trends.tf`). The **only**
non-Terraform step is building the container image — Terraform doesn't build
images, so we build with Cloud Build and Terraform references the pushed image.

## Architecture

```
                        build (Cloud Build) ─────────────► Artifact Registry: gtrends/job
                                                                     │ image
Cloud Scheduler ─(OAuth :run)─► Cloud Run Job  gtrends-daily ◄───────┤   national + DMA snapshot + now-7d HOURLY
   (daily 9am PT)                                                     │
        (on demand) ──────────► Cloud Run Job  gtrends-backfill ◄─────┘   national + DMA snapshot (+ per-DMA daily)
                                        │ runs as SA gtrends-ingest
                                        ▼
                       gs://…-raw/google_trends/dt=<date>/…json   (bronze)
```

Both jobs run `google_trends_api/job.py`, regenerate the roster from BigQuery
`tm_events`, shard work across tasks, and skip units already landed today
(idempotent / resumable).

## Prerequisites

- `gcloud auth login` and `gcloud auth application-default login`
- `terraform/terraform.tfvars` has `project_id`, `alert_email`, `ticketmaster_api_key`
- You have `roles/resourcemanager.projectIamAdmin` (for the SA role bindings) — granted 2026-06-13.

## Deploy (one-time, and on any code change)

```bash
cd terraform
terraform init

# 1. Create the Artifact Registry repo first — the jobs reference an image in it.
terraform apply -target=google_artifact_registry_repository.gtrends

# 2. Build + push the job image (from the REPO ROOT).
cd ..
gcloud builds submit --config google_trends_api/cloudbuild.yaml \
  --substitutions=_REGION=us-west1,_PROJECT=data-architecture-498123,_TAG=latest .

# 3. Apply the rest (SA + project-level IAM, both jobs, scheduler, monitoring).
cd terraform
terraform apply
```

## Run the backfill (on demand)

Cheap-first is the default (national interest + all-DMA snapshot per artist):

```bash
gcloud run jobs execute gtrends-backfill --region us-west1
```

Then the **deep per-DMA daily** pass (the expensive ~7k-call run) — flip the
Terraform var so it's tracked in state, re-apply, and execute:

```bash
cd terraform
terraform apply -var='gtrends_backfill_include_dma=true'
gcloud run jobs execute gtrends-backfill --region us-west1
```

Watch progress:

```bash
gcloud run jobs executions list --job gtrends-backfill --region us-west1
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="gtrends-backfill"' --limit 20 --freshness 1h
gsutil du -sh gs://data-architecture-498123-raw/google_trends/
```

## Daily job

`gtrends-daily` runs automatically on the Cloud Scheduler cron (`var.gtrends_schedule`,
default 9am PT) — national + DMA snapshot refresh + the `now 7-d` hourly capture.
Trigger a manual run any time:

```bash
gcloud run jobs execute gtrends-daily --region us-west1
```

## Tuning (Terraform vars)

| var | default | meaning |
|---|---|---|
| `gtrends_top_n` | 250 | top touring artists selected (plus the curated seed) |
| `gtrends_backfill_tasks` | 4 | parallel backfill shards |
| `gtrends_sleep_seconds` | 12 | polite pause between Trends calls (429 avoidance) |
| `gtrends_backfill_include_dma` | false | deep per-DMA pass on/off |
| `gtrends_schedule` | `0 9 * * *` | daily job cron (America/Los_Angeles) |
| `gtrends_daily_max_units` | 0 | cap units per daily run (0 = all) |
