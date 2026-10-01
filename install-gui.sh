#!/bin/sh
# Installs the Today window on this Mac. Safe to run again (it repairs whatever is missing).
#   sh ~/.config/today/install-gui.sh
#
# 1. a private Python environment in ~/.config/today/venv with pywebview (native macOS window)
# 2. ~/Applications/Today.app, so it's in Spotlight, Launchpad and the Dock like any app
# 3. the login item now opens the Today window instead of a Terminal window
#
set -e
APP="$HOME/.config/today"
VENV="$APP/venv"
BUNDLE="$HOME/Applications/Today.app"
AGENTS="$HOME/Library/LaunchAgents"
PLIST="$AGENTS/com.today.scheduler.plist"

# ---- 1. Python + pywebview -------------------------------------------------------------
if ! "$VENV/bin/python" -c 'import webview' >/dev/null 2>&1; then
  PY=""
  # Apple's python3 (from the command line tools) first: it doesn't move under Homebrew upgrades.
  if xcode-select -p >/dev/null 2>&1; then set -- /usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3
  else set -- /opt/homebrew/bin/python3 /usr/local/bin/python3; fi
  for c in "$@"; do
    if [ -x "$c" ] && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY="$c"; break; fi
  done
  [ -n "$PY" ] || { echo "Need Python 3.9 or newer: xcode-select --install  (or: brew install python)"; exit 1; }
  echo "Setting up the window's Python environment with $PY (one time, about a minute)..."
  [ -d "$VENV" ] && mv "$VENV" "$VENV.broken.$$"
  "$PY" -m venv "$VENV"
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check "pywebview>=5"
  "$VENV/bin/python" -c 'import webview' || { echo "pywebview failed to install (see the pip messages above)"; exit 1; }
  echo "  ok"
fi

# ---- 2. Today.app --------------------------------------------------------------------------
echo "Building $BUNDLE ..."
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"
cat > "$BUNDLE/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleName</key><string>Today</string>
	<key>CFBundleDisplayName</key><string>Today</string>
	<key>CFBundleIdentifier</key><string>com.today.scheduler.app</string>
	<key>CFBundleExecutable</key><string>Today</string>
	<key>CFBundleIconFile</key><string>Today</string>
	<key>CFBundlePackageType</key><string>APPL</string>
	<key>CFBundleShortVersionString</key><string>2.0</string>
	<key>LSMinimumSystemVersion</key><string>11.0</string>
	<key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF
cat > "$BUNDLE/Contents/MacOS/Today" <<'EOF'
#!/bin/sh
# Launches the Today window (~/.config/today/today_gui.py). Errors go to /tmp/today-gui.log.
exec "$HOME/.config/today/venv/bin/python" "$HOME/.config/today/today_gui.py" "$@" 2>>/tmp/today-gui.log
EOF
chmod +x "$BUNDLE/Contents/MacOS/Today"

if [ -f "$APP/icon.png" ]; then
  TMP=$(mktemp -d)
  mkdir "$TMP/Today.iconset"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$APP/icon.png" --out "$TMP/Today.iconset/icon_${s}x${s}.png" >/dev/null
    d=$((s * 2))
    sips -z $d $d "$APP/icon.png" --out "$TMP/Today.iconset/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$TMP/Today.iconset" -o "$BUNDLE/Contents/Resources/Today.icns"
  rm -r "$TMP"
fi
xattr -dr com.apple.quarantine "$BUNDLE" "$APP" 2>/dev/null || true
touch "$BUNDLE"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$BUNDLE" 2>/dev/null || true
echo "  ok"

# ---- 3. login item: open the window instead of Terminal ------------------------------------
mkdir -p "$AGENTS"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>com.today.scheduler</string>
	<key>ProgramArguments</key>
	<array>
		<string>/usr/bin/open</string>
		<string>$BUNDLE</string>
	</array>
	<key>RunAtLoad</key>
	<true/>
	<key>StandardErrorPath</key>
	<string>/tmp/today-login.err</string>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)/com.today.scheduler" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Login item now opens the Today window (it just fired once, so the window should be opening)."
echo
echo "Open it any time from Spotlight (Today) or ~/Applications."
