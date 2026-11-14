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
  echo "harness checkout has local changes; assuming patches are already applied"
else
  git checkout -q "$BASE"
fi
for p in browser-harness-deepinfra browser-harness-drex-alias; do
  if git apply --reverse --check "../../patches/$p.patch" 2>/dev/null; then
    echo "$p already applied"
  elif git apply --check "../../patches/$p.patch" 2>/dev/null; then
    git apply "../../patches/$p.patch"; echo "applied $p"
  else
    echo "ERROR: $p does not apply to $BASE" >&2; exit 1
  fi
done
npm ci && npm run build:release && node runner/bin/nace-browser.mjs install
