#!/usr/bin/env bash
# Builds the Nace browser harness at the commit Reach was tested against, with Reach's small patches (the third adds DeepInfra to the Chrome extension).
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
  # the extension patch builds on the other two (it touches lines they added), so check it first: if it reverses
  # cleanly all three are in; otherwise the first two must be.
  if git apply --reverse --check "../../patches/browser-harness-extension-deepinfra.patch" 2>/dev/null; then
    echo "harness at $BASE with all three patches already applied"
  else
    for p in browser-harness-deepinfra browser-harness-drex-alias; do
      if ! git apply --reverse --check "../../patches/$p.patch" 2>/dev/null; then
        echo "ERROR: harness checkout has local changes but $p is not applied; reset it or delete vendor/drex-browser-harness" >&2
        exit 1
      fi
    done
    p=browser-harness-extension-deepinfra
    if git apply --check "../../patches/$p.patch" 2>/dev/null; then
      git apply "../../patches/$p.patch"; echo "applied $p"
    else
      echo "ERROR: $p does not apply on top of the other two" >&2; exit 1
    fi
  fi
else
  git checkout -q "$BASE"
  for p in browser-harness-deepinfra browser-harness-drex-alias browser-harness-extension-deepinfra; do
    if git apply --check "../../patches/$p.patch" 2>/dev/null; then
      git apply "../../patches/$p.patch"; echo "applied $p"
    else
      echo "ERROR: $p does not apply to $BASE" >&2; exit 1
    fi
  done
fi
npm ci && npm run build:release && node runner/bin/nace-browser.mjs install
