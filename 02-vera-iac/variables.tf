variable "subscription_id" {
  type    = string
  default = "8703c97b-a5bf-484d-b8d0-f739f22aec8e"
}

variable "resource_group_name" {
  type    = string
  default = "rg-vera-202610250146"
}

variable "prefix" {
  type    = string
  default = "vera"
}

variable "image_tag" {
  description = "Tag of the tacto image in the registry; deploy.sh builds and passes it."
  type        = string
  default     = "latest"
}

variable "arena_url" {
  type    = string
  default = ""
}

variable "arena_api_key" {
  type      = string
  sensitive = true
}

variable "anthropic_api_key" {
  type      = string
  sensitive = true
}

variable "llm_env" {
  description = "Buyer model settings, as in docs/FINAL_RUNBOOK.md."
  type        = map(string)
  default = {
    LLM_PROVIDER = "anthropic"
    LLM_MODEL    = "claude-opus-5-5"
    LLM_EFFORT   = "high"
    LLM_SPEED    = "fast"
  }
}

variable "buyer_command" {
  type    = list(string)
  default = ["tacto", "run", "--set", "final", "--agent", "routed", "--data", "data", "--parallel", "10", "--waves", "5"]
}
