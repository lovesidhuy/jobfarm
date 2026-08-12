output "persistent_volume_id" {
  value = aws_ebs_volume.jobbots.id
}

output "profile_lease_table_name" {
  value = aws_dynamodb_table.profile_leases.name
}

output "profile_lease_table_arn" {
  value = aws_dynamodb_table.profile_leases.arn
}

output "artifact_bucket_name" {
  value = aws_s3_bucket.artifacts.id
}

output "artifact_bucket_arn" {
  value = aws_s3_bucket.artifacts.arn
}

output "runtime_secret_name" {
  value = aws_secretsmanager_secret.runtime.name
}

output "runtime_secret_arn" {
  value = aws_secretsmanager_secret.runtime.arn
}
