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

variable "force_destroy_buckets" {
  description = "Allow `terraform destroy` to delete buckets that still contain objects. Safe for a class project; flip to false before prod."
  type        = bool
  default     = true
}
