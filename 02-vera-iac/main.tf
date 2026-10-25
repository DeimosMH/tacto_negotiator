data "azurerm_resource_group" "this" {
  name = var.resource_group_name
}

locals {
  location   = data.azurerm_resource_group.this.location
  image      = "${azurerm_container_registry.this.login_server}/tacto:${var.image_tag}"
  docs_image = "${azurerm_container_registry.this.login_server}/tacto-docs:${var.image_tag}"
  ui_host    = "ca-${var.prefix}-ui.${azurerm_container_app_environment.this.default_domain}"
}

resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

# Registry (the ECR equivalent). Apps pull with a managed identity, no admin password.
resource "azurerm_container_registry" "this" {
  name                = "acr${var.prefix}${random_string.suffix.result}"
  resource_group_name = data.azurerm_resource_group.this.name
  location            = local.location
  sku                 = "Basic"
  admin_enabled       = false
}

resource "azurerm_user_assigned_identity" "pull" {
  name                = "id-${var.prefix}-acrpull"
  resource_group_name = data.azurerm_resource_group.this.name
  location            = local.location
}

resource "azurerm_role_assignment" "pull" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.pull.principal_id
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "log-${var.prefix}"
  resource_group_name = data.azurerm_resource_group.this.name
  location            = local.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

# Container Apps environment (the ECS cluster equivalent).
resource "azurerm_container_app_environment" "this" {
  name                       = "cae-${var.prefix}"
  resource_group_name        = data.azurerm_resource_group.this.name
  location                   = local.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  # Azure adds this profile anyway; declaring it keeps plans clean.
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

# Live docs share: sync-docs.sh mirrors docs/ (atlas, guides, reports) here and the buyer writes
# every deal move here. The docs site and the dashboard serve it directly.
resource "azurerm_storage_account" "journal" {
  name                     = "st${var.prefix}${random_string.suffix.result}"
  resource_group_name      = data.azurerm_resource_group.this.name
  location                 = local.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"
}

resource "azurerm_storage_share" "journal" {
  name               = "journal"
  storage_account_id = azurerm_storage_account.journal.id
  quota              = 5
}

resource "azurerm_container_app_environment_storage" "journal" {
  name                         = "journal"
  container_app_environment_id = azurerm_container_app_environment.this.id
  account_name                 = azurerm_storage_account.journal.name
  share_name                   = azurerm_storage_share.journal.name
  access_key                   = azurerm_storage_account.journal.primary_access_key
  access_mode                  = "ReadWrite"
}

# Docs atlas (docs/index.html), guides and deal journal, served live from the share.
resource "azurerm_container_app" "docs" {
  name                         = "ca-${var.prefix}-docs"
  resource_group_name          = data.azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  depends_on                   = [azurerm_role_assignment.pull]

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.pull.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.pull.id
  }

  ingress {
    external_enabled = true
    target_port      = 80
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0
    max_replicas = 1
    volume {
      name         = "journal"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.journal.name
    }
    container {
      name   = "docs"
      image  = local.docs_image
      cpu    = 0.25
      memory = "0.5Gi"
      env {
        name  = "DASHBOARD_URL"
        value = "https://${local.ui_host}"
      }
      volume_mounts {
        name = "journal"
        path = "/mnt/docs"
      }
    }
  }
}

# Local supplier API. Sessions live in memory, so exactly one replica.
resource "azurerm_container_app" "sim" {
  name                         = "ca-${var.prefix}-sim"
  resource_group_name          = data.azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  depends_on                   = [azurerm_role_assignment.pull]

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.pull.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.pull.id
  }

  ingress {
    external_enabled = true
    target_port      = 8080
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 1
    container {
      name   = "sim"
      image  = local.image
      cpu    = 0.5
      memory = "1Gi"
    }
  }
}

# Read-only results dashboard over the live reports on the share (docs/results).
resource "azurerm_container_app" "ui" {
  name                         = "ca-${var.prefix}-ui"
  resource_group_name          = data.azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  depends_on                   = [azurerm_role_assignment.pull]

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.pull.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.pull.id
  }

  ingress {
    external_enabled = true
    target_port      = 1234
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0
    max_replicas = 1
    volume {
      name         = "journal"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.journal.name
    }
    container {
      name   = "ui"
      image  = local.image
      cpu    = 0.25
      memory = "0.5Gi"
      command = ["tacto-ui", "--host", "0.0.0.0", "--port", "1234", "--reports", "/mnt/docs/results",
      "--allow-host", local.ui_host]
      volume_mounts {
        name = "journal"
        path = "/mnt/docs"
      }
    }
  }
}

# Buyer: a manually started job (the ECS RunTask equivalent). It contacts the hosted arena.
resource "azurerm_container_app_job" "buyer" {
  name                         = "caj-${var.prefix}-buyer"
  resource_group_name          = data.azurerm_resource_group.this.name
  location                     = local.location
  container_app_environment_id = azurerm_container_app_environment.this.id
  replica_timeout_in_seconds   = 4 * 3600
  replica_retry_limit          = 0
  workload_profile_name        = "Consumption"
  depends_on                   = [azurerm_role_assignment.pull]

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.pull.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.pull.id
  }

  secret {
    name  = "arena-api-key"
    value = var.arena_api_key
  }

  secret {
    name  = "anthropic-api-key"
    value = var.anthropic_api_key
  }

  template {
    volume {
      name         = "journal"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.journal.name
    }
    container {
      name    = "buyer"
      image   = local.image
      cpu     = 1
      memory  = "2Gi"
      command = var.buyer_command

      env {
        name  = "ARENA_URL"
        value = var.arena_url
      }
      env {
        name  = "ARENA_DATA"
        value = "data"
      }
      env {
        name  = "TACTO_DEALS_DIR"
        value = "/mnt/docs/deals"
      }
      volume_mounts {
        name = "journal"
        path = "/mnt/docs"
      }
      env {
        name        = "ARENA_API_KEY"
        secret_name = "arena-api-key"
      }
      env {
        name        = "LLM_API_KEY_ANTHROPIC"
        secret_name = "anthropic-api-key"
      }
      dynamic "env" {
        for_each = var.llm_env
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
}
