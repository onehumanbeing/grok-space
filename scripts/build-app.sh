#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$PROJECT_ROOT/out/Grok Spaces.app"
CONTENTS="$APP/Contents"
RESOURCES="$CONTENTS/Resources"
ICON_SOURCE="$PROJECT_ROOT/assets/grok-space.png"
read -r APP_VERSION APP_BUILD < <(/usr/bin/python3 -E -s -B -c \
  'import runpy, sys; v = runpy.run_path(sys.argv[1]); print(v["VERSION"], v["BUILD_NUMBER"])' \
  "$PROJECT_ROOT/bridge/version.py")

for required in "$PROJECT_ROOT/bridge/server.py" "$PROJECT_ROOT/web" "$PROJECT_ROOT/scripts/grok-space" "$ICON_SOURCE"; do
  if [[ ! -e "$required" ]]; then
    echo "Missing app resource: $required" >&2
    exit 1
  fi
done

mkdir -p "$CONTENTS/MacOS" "$RESOURCES"
ICON_WORKDIR=$(mktemp -d "$PROJECT_ROOT/out/grok-space-icon.XXXXXX")
trap 'rm -rf "$ICON_WORKDIR"' EXIT
ICONSET="$ICON_WORKDIR/GrokSpace.iconset"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  /usr/bin/sips -z "$size" "$size" "$ICON_SOURCE" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double_size=$((size * 2))
  /usr/bin/sips -z "$double_size" "$double_size" "$ICON_SOURCE" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
/usr/bin/iconutil -c icns "$ICONSET" -o "$RESOURCES/GrokSpace.icns"
xcrun swiftc -swift-version 6 -O -target "$(uname -m)-apple-macosx14.0" \
  -framework AppKit -framework WebKit -framework CoreGraphics \
  "$PROJECT_ROOT/native/ServerProcess.swift" \
  "$PROJECT_ROOT/native/CompanionCoordinator.swift" \
  "$PROJECT_ROOT/native/CompanionGeometry.swift" \
  "$PROJECT_ROOT/native/SmokeCheck.swift" \
  "$PROJECT_ROOT/native/SpaceLink.swift" \
  "$PROJECT_ROOT/native/RootActivityCoordinator.swift" \
  "$PROJECT_ROOT/native/LifecycleReport.swift" \
  "$PROJECT_ROOT/native/OwnWindowCoordinator.swift" \
  "$PROJECT_ROOT/native/main.swift" \
  -o "$CONTENTS/MacOS/GrokSpaces"

# Remove only generated resource copies; source files and user data are untouched.
rm -rf "$RESOURCES/bridge" "$RESOURCES/web"
/usr/bin/ditto --norsrc "$PROJECT_ROOT/bridge" "$RESOURCES/bridge"
/usr/bin/ditto --norsrc "$PROJECT_ROOT/web" "$RESOURCES/web"
/usr/bin/ditto --norsrc "$PROJECT_ROOT/scripts/grok-space" "$RESOURCES/grok-space"
chmod 755 "$RESOURCES/grok-space"
ln -sf ../Resources/grok-space "$CONTENTS/MacOS/grok-space"
find "$RESOURCES" -type d -name __pycache__ -prune -exec rm -rf {} +

cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>sh.henry.grok-spaces</string>
  <key>CFBundleName</key><string>Grok Spaces</string>
  <key>CFBundleDisplayName</key><string>Grok Spaces</string>
  <key>CFBundleExecutable</key><string>GrokSpaces</string>
  <key>CFBundleIconFile</key><string>GrokSpace.icns</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>$APP_BUILD</string>
  <key>CFBundleShortVersionString</key><string>$APP_VERSION</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>CFBundleURLTypes</key><array><dict>
    <key>CFBundleURLName</key><string>sh.henry.grok-spaces.selection</string>
    <key>CFBundleURLSchemes</key><array><string>grok-space</string></array>
    <key>CFBundleTypeRole</key><string>Viewer</string>
  </dict></array>
  <key>NSAppTransportSecurity</key><dict>
    <key>NSAllowsLocalNetworking</key><true/>
  </dict>
</dict></plist>
PLIST

/usr/bin/plutil -lint "$CONTENTS/Info.plist"
/usr/bin/codesign --force --sign - "$APP"
echo "Built: $APP"
