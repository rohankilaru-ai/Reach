#!/usr/bin/env bash
# Builds the Nace browser harness at the commit Reach was tested against, with Reach's two small patches.
# Needs Node >= 22.18 and git. Fails (non-zero) if a patch cannot be applied, so you never get an unpatched build by accident.
set -euo pipefail
BASE=9f09a8c
cd "$(dirname "$0")/.."
mkdir -p vendor
[ -d vendor/drex-browser-harness ] || git clone https://github.com/nace-ai/drex-browser-harness-pub vendor/drex-browser-harness
cd vendor/drex-browser-harness
if ! git diff --quiet || [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  # Local changes: only accept them if this is the pinned commit with exactly Reach's patches applied.
  head=$(git rev-parse HEAD)
  base=$(git rev-parse "$BASE^{commit}")
  if [ "$head" != "$base" ]; then
    echo "ERROR: harness checkout has local changes and HEAD is $head, not $BASE; reset it or delete vendor/drex-browser-harness" >&2
    exit 1
  fi
  for p in browser-harness-deepinfra browser-harness-drex-alias; do
    if ! git apply --reverse --check "../../patches/$p.patch" 2>/dev/null; then
      echo "ERROR: harness checkout has local changes but $p is not applied; reset it or delete vendor/drex-browser-harness" >&2
      exit 1
    fi
  done
  echo "harness at $BASE with both patches already applied"
else
  git checkout -q "$BASE"
  for p in browser-harness-deepinfra browser-harness-drex-alias; do
    if git apply --check "../../patches/$p.patch" 2>/dev/null; then
      git apply "../../patches/$p.patch"; echo "applied $p"
    else
      echo "ERROR: $p does not apply to $BASE" >&2; exit 1
    fi
  done
fi
npm ci && npm run build:release && node runner/bin/nace-browser.mjs install
