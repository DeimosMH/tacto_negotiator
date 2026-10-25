#!/usr/bin/env bash
# Mirror docs/ to the Azure share that the deployed docs site and dashboard serve live:
# atlas, guides, deal journal and dashboard reports (docs/results). The atlas (data.js) is
# regenerated from the repository whenever sources or data change; the committed docs/data.js
# is never touched.
#   sync-docs.sh          upload what changed since the last sync (everything the first time)
#   sync-docs.sh --full   upload everything
#   sync-docs.sh --watch  keep syncing every 5 s (tacto-docs-sync.service runs this)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(dirname "$here")"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/tacto-docs-sync"
site="$cache/site"
mode="${1:-}"
mkdir -p "$site"

cd "$here"
account="${DOCS_STORAGE:-$(terraform output -raw journal_storage)}"
group="${RESOURCE_GROUP:-$(terraform output -raw resource_group)}"
share=journal
export AZURE_STORAGE_ACCOUNT="$account"
AZURE_STORAGE_KEY="$(az storage account keys list -g "$group" -n "$account" --query '[0].value' -o tsv)"
export AZURE_STORAGE_KEY

# Anything the atlas reads: sources, tests, data, guides, catalog and the root files.
atlas_inputs() {
  find "$root/src" "$root/tests" "$root/data" "$root"/docs/*.md "$root"/docs/*.py \
    "$root"/{README.md,.gitignore,.dockerignore,.env.example,pyproject.toml,uv.lock,Dockerfile,LICENSE,LICENSE-data,NOTICE.md} \
    -type f -newer "$cache/atlas.stamp" -not -path '*/__pycache__/*' -print -quit 2>/dev/null
}

# Stage docs/ (minus scripts) and print "+ path" to upload or "- path" to delete.
stage() {
  rsync -a --delete --exclude='__pycache__/' --exclude='*.py' --exclude='*.mjs' --exclude='/data.js' \
    --out-format='%i %n' "$root/docs/" "$site/" |
    awk '$1 == "*deleting" {print "- " $2; next} substr($1, 2, 1) == "f" {print "+ " $2}'
  if [[ ! -f "$cache/atlas.stamp" || -n "$(atlas_inputs)" ]]; then
    touch "$cache/atlas.stamp.next"
    (cd "$root/docs" && python3 -B -c \
      'import build, sys; open(sys.argv[1], "w", encoding="utf-8").write(build.serialize(build.generate()))' \
      "$cache/data.js")
    mv "$cache/atlas.stamp.next" "$cache/atlas.stamp"
    if ! cmp -s "$cache/data.js" "$site/data.js"; then
      mv "$cache/data.js" "$site/data.js"
      echo "+ data.js"
    fi
  fi
}

upload_one() {
  local rel="$1" dir
  dir="$(dirname "$rel")"
  [[ "$dir" == . ]] || az storage directory create --share-name "$share" --name "$dir" -o none 2>/dev/null || true
  az storage file upload --share-name "$share" --source "$site/$rel" --path "$rel" --no-progress -o none
}
export -f upload_one
export share site

apply() {  # read "+ path" / "- path" lines; upload in parallel, the journal index last
  local changes up=() down=() index=0
  mapfile -t changes
  for line in "${changes[@]}"; do
    case "$line" in
      "+ deals.js") index=1 ;;
      "+ "*) up+=("${line#+ }") ;;
      "- "*) down+=("${line#- }") ;;
    esac
  done
  if ((${#up[@]})); then printf '%s\0' "${up[@]}" | xargs -0 -P 8 -I{} bash -c 'upload_one "$1"' _ {}; fi
  ((index)) && upload_one deals.js
  for rel in "${down[@]}"; do
    az storage file delete --share-name "$share" --path "$rel" -o none 2>/dev/null || true
  done
  local total=$((${#up[@]} + index + ${#down[@]}))
  ((total)) && echo "$(date +%T) synced $total file(s) to $account/$share"
  return 0
}

if [[ "$mode" == --full ]]; then
  rm -f "$cache/atlas.stamp"
  stage >/dev/null
  az storage file upload-batch --destination "$share" --source "$site" --no-progress -o none
  echo "$(date +%T) uploaded the whole site ($(find "$site" -type f | wc -l) files) to $account/$share"
else
  stage | apply
fi

[[ "$mode" == --watch ]] || exit 0
echo "watching $root/docs and the atlas sources (every 5 s)"
while sleep 5; do
  stage | apply || echo "$(date +%T) sync failed; retrying" >&2
done
