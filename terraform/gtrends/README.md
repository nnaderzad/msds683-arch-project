# terraform/gtrends/ — collectors root (REMOTE state — anyone can apply)

Isolated Terraform root for the Google Trends, YouTube, and scene-listing
collectors. Deliberately separate from the main [`../`](../README.md) root:
that one has local state and needs the Ticketmaster key, so it can't be applied
from another laptop. This root keeps state in a **remote GCS backend**
(`data-architecture-498123-tfstate`, prefix `gtrends`) and refers to shared
infra (raw bucket, dataset) **by name, not as resources** — any teammate can
plan/apply it without collisions.

## What it provisions

| File | Resources |
|---|---|
| `main.tf` | Artifact Registry repo + shared job image, `gtrends-daily` (11:00 PT) + `gtrends-backfill` (manual) Cloud Run Jobs, scheduler SA, Trends failure alert + **project-wide job-execution-failure alert** |
| `youtube.tf` | `youtube-daily` job (15:00 PT) + scheduler, dedicated SA, reads the `youtube-api-key` secret |
| `scene.tf` | `nineteenhz-daily` (08:00 PT) + `ra-daily` (08:15 PT) jobs + schedulers, bronze-only scene SA |

All four jobs share one container image (`gtrends_image_tag` var). The Trends
jobs also share a single global rate budget (`TRENDS_SLEEP` ≥ 20 s,
single-stream; `DAILY_CALL_BUDGET` 800/day counted from bronze) — never add
parallel streams, GCP datacenter IPs are throttle-flagged.

## Apply workflow

```bash
gcloud auth application-default login
terraform -chdir=terraform/gtrends init
# terraform.tfvars (gitignored) needs project_id + alert_email; rest has defaults
terraform -chdir=terraform/gtrends plan
terraform -chdir=terraform/gtrends apply
```

Building/pushing the shared image (Cloud Build) and rolling a new tag:
[`../../google_trends_api/DEPLOY.md`](../../google_trends_api/DEPLOY.md).

⚠️ `ra-daily` makes THE one Resident Advisor request per day allowed by the
written agreement ([`../../ra_api/README.md`](../../ra_api/README.md)) — never
execute it manually on a day the schedule already ran. Live schedules and
deploy status: [`../../docs/REPO_STATE.md`](../../docs/REPO_STATE.md).
