#!/usr/bin/env bash
# Builds the Nace browser harness with Reach's DeepInfra helper patch. Needs Node >= 22.18 and gh or git.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p vendor
[ -d vendor/drex-browser-harness ] || git clone --depth 1 https://github.com/nace-ai/drex-browser-harness-pub vendor/drex-browser-harness
cd vendor/drex-browser-harness
git apply --check ../../patches/browser-harness-deepinfra.patch 2>/dev/null && git apply ../../patches/browser-harness-deepinfra.patch || echo "patch already applied or does not apply cleanly; check manually"
npm ci && npm run build:release && node runner/bin/nace-browser.mjs install
