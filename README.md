# Event Demand Forecasting — Data Architecture (MSDS 683)

End-to-end data architecture for predicting demand for electronic music events
in the Bay Area, combining ticket-market signals (SeatGeek), artist popularity
(Spotify), and local interest (Google Trends).

## Repo layout

```
arch_project/
├── Project plan.pdf       # class assignment
├── README.md              # this file
└── terraform/             # GCP infrastructure (Bonus +20 pts)
    ├── providers.tf       # google provider + version pinning
    ├── variables.tf       # project_id, region, dataset name
    ├── apis.tf            # enables storage + bigquery APIs
    ├── storage.tf         # 3 GCS buckets: raw / processed / analytics
    ├── bigquery.tf        # event_demand_analytics dataset
    ├── outputs.tf         # echoes resource names after apply
    ├── terraform.tfvars.example
    └── .gitignore
```

## Architecture layers

| Layer     | GCS bucket                              | Holds                                              |
|-----------|-----------------------------------------|----------------------------------------------------|
| Bronze    | `data-architecture-498123-raw`          | Raw JSON from SeatGeek, Spotify, Google Trends     |
| Silver    | `data-architecture-498123-processed`    | Cleaned + joined event-artist records              |
| Gold      | `data-architecture-498123-analytics`    | Model-ready features                               |
| Warehouse | BigQuery `event_demand_analytics`       | Cleaned + analytical tables                        |

Versioning is enabled on the raw bucket so the daily SeatGeek/Spotify snapshots
can be re-read by hash if a pipeline run gets re-played.

## Prerequisites

Install once on your machine:

```bash
brew install terraform
brew install --cask google-cloud-sdk
```

Authenticate gcloud against your GCP account (one-time, browser-based):

```bash
gcloud auth application-default login
gcloud config set project data-architecture-498123
```

Confirm billing is enabled on the project at
https://console.cloud.google.com/billing — required for storage + BigQuery.

## Provisioning the infrastructure

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars     # already filled with project_id
terraform init                                    # downloads google provider
terraform plan                                    # preview what will be created
terraform apply                                   # type 'yes' to confirm
```

`terraform apply` is what the +20 bonus rubric asks you to demonstrate.

After it succeeds, Terraform will echo the resource names. Verify in the GCP
console:
- Buckets → https://console.cloud.google.com/storage/browser
- BigQuery → https://console.cloud.google.com/bigquery

## Tearing it down

```bash
cd terraform
terraform destroy
```

`force_destroy_buckets = true` (in `variables.tf`) lets destroy wipe buckets
that still contain objects — convenient for a class project, flip to `false`
before anything resembling production.

## Estimated cost

For demo-scale data (a few GB across all buckets, a few BigQuery queries):
- GCS: ~$0.02/GB/month standard storage → pennies/month
- BigQuery: 10 GB storage + 1 TB query free tier → effectively free
- No Cloud Composer, no Dataflow → no idle compute cost

You can run this all term well under $5 with the $300 GCP new-account credit.
