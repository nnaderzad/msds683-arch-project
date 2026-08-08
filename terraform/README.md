# terraform/ — main infrastructure root (LOCAL state)

Provisions the shared platform plus the Ticketmaster pipeline and gold-refresh
job. **State is local** (`terraform.tfstate` on the maintainer's machine — see
`providers.tf`), so **only the state holder can plan/apply this root**.
Everyone else changes these components via the gcloud paths documented in
[`../docs/REPO_STATE.md`](../docs/REPO_STATE.md) — e.g.
`cloud_functions/ticketmaster_daily/deploy.sh` for the TM function, `gcloud run
jobs update` for a new gold-refresh image. The Trends/YouTube/scene collectors
live in the separate [`gtrends/`](gtrends/README.md) root (remote state —
anyone can apply).

## What's in here

| File | Resources |
|---|---|
| `apis.tf` | project service enablement (GCS, BigQuery, Cloud Functions/Build/Run, Scheduler, …) |
| `storage.tf` | the three medallion buckets `<project>-{raw,processed,analytics}` (raw is versioned) |
| `bigquery.tf` | the `event_demand_analytics` dataset |
| `ticketmaster_scheduler.tf` | TM daily extract: source zip → Cloud Function gen2 + Scheduler; API key injected from Secret Manager, never in source |
| `gold_refresh_job.tf` | Artifact Registry repo, Cloud Build image build, `gold-refresh` Cloud Run Job + least-privilege SA + Scheduler |
| `monitoring.tf` | email alert policy on ERROR-severity logs from the TM function/scheduler |

## Apply (state holder only)

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # project_id, alert_email, …
gcloud auth application-default login
terraform init
terraform plan
terraform apply
```

CI runs `terraform fmt -check -recursive` plus `terraform init -backend=false
&& terraform validate` on **both** roots for every push (no plan/apply — CI has
no credentials), so keep files `terraform fmt`-clean.

Migrating this root to the remote GCS backend (like `gtrends/`) is a known
follow-on — the backend block is stubbed out in `providers.tf`.
