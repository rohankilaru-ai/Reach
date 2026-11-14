#!/usr/bin/env bash
# Builds the Reach overlay and wraps it in a minimal, ad-hoc signed Reach.app.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PKG="$REPO/apps/overlay"
APP="$PKG/build/Reach.app"

swift build -c release --package-path "$PKG"
BIN="$(swift build -c release --package-path "$PKG" --show-bin-path)/Reach"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$BIN" "$APP/Contents/MacOS/Reach"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>dev.reach.overlay</string>
  <key>CFBundleName</key><string>Reach</string>
  <key>CFBundleExecutable</key><string>Reach</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
  <key>NSScreenCaptureUsageDescription</key><string>Reach reads the region you select so it can answer about it.</string>
  <key>ReachRepo</key><string>$REPO</string>
</dict></plist>
PLIST
codesign --force -s - "$APP"
echo "built $APP"
