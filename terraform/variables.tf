variable "project_id" {
  description = "GCP project ID that owns the resources."
  type        = string
}

variable "region" {
  description = "Default region for buckets and the BigQuery dataset."
  type        = string
  default     = "us-west1"
}

variable "bigquery_dataset" {
  description = "BigQuery dataset for cleaned + analytical tables."
  type        = string
  default     = "event_demand_analytics"
}

variable "ticketmaster_api_key" {
  description = "Ticketmaster Discovery API consumer key. Set it in terraform.tfvars (git-ignored); Terraform stores it in Secret Manager for the daily extract function."
  type        = string
  sensitive   = true
}

variable "ticketmaster_schedule" {
  description = "Cron schedule for the Ticketmaster extract (interpreted in America/Los_Angeles). The MAX_CALLS_PER_RUN quota math assumes 2 runs/day — adding runs needs that budget rechecked."
  type        = string
  default     = "0 6,18 * * *" # 06:00 and 18:00 Pacific
}

variable "alert_email" {
  description = "Email address that receives pipeline failure alerts (set in terraform.tfvars)."
  type        = string
}

variable "force_destroy_buckets" {
  description = "Allow `terraform destroy` to delete buckets that still contain objects. Safe for a class project; flip to false before prod."
  type        = bool
  default     = true
}
