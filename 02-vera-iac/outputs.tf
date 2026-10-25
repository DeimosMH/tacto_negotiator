output "resource_group" {
  value = data.azurerm_resource_group.this.name
}

output "registry" {
  value = azurerm_container_registry.this.name
}

output "image" {
  value = local.image
}

output "sim_url" {
  value = "https://${azurerm_container_app.sim.ingress[0].fqdn}"
}

output "ui_url" {
  value = "https://${azurerm_container_app.ui.ingress[0].fqdn}"
}

output "buyer_job" {
  value = azurerm_container_app_job.buyer.name
}

output "docs_url" {
  value = "https://${azurerm_container_app.docs.ingress[0].fqdn}"
}

output "journal_storage" {
  value = azurerm_storage_account.journal.name
}
