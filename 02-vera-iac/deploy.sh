#!/usr/bin/env bash
# Build the image in ACR and deploy everything. Needs `az login`; reads keys from ../.env.
set -euo pipefail
cd "$(dirname "$0")"
root=..

source ./env.sh

tag="${IMAGE_TAG:-$(git -C "$root" rev-parse --short HEAD)$(git -C "$root" diff --quiet HEAD -- || echo "-dirty-$(date +%Y%m%d%H%M%S)")}"

terraform init -input=false
# The registry must exist before the image can be built into it.
terraform apply -input=false -auto-approve -target=azurerm_container_registry.this
registry="$(terraform output -raw registry)"
(cd "$root" && az acr build --registry "$registry" --image "tacto:$tag" --file Dockerfile.azure .)
(cd "$root" && az acr build --registry "$registry" --image "tacto-docs:$tag" --file Dockerfile.docs .)
terraform apply -input=false -auto-approve -var "image_tag=$tag"
# Publish the docs site, reports and deal journal to the live share.
./sync-docs.sh --full
terraform output
