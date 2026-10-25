# Source before plain terraform commands: `source env.sh && terraform plan`.
# Reads the arena URL and keys from the repository .env; process variables win.
_tacto_env="$(dirname "${BASH_SOURCE[0]}")/../.env"
_env_value() { grep -E "^$1=" "$_tacto_env" | tail -1 | cut -d= -f2-; }
export TF_VAR_arena_url="${ARENA_URL:-$(_env_value ARENA_URL)}"
export TF_VAR_arena_api_key="${ARENA_API_KEY:-$(_env_value ARENA_API_KEY)}"
export TF_VAR_anthropic_api_key="${LLM_API_KEY_ANTHROPIC:-$(_env_value LLM_API_KEY_ANTHROPIC)}"
