#!/bin/sh
# Finishes the `today` install on this Mac. Safe to run more than once.
#   sh ~/.config/today/install-login-item.sh
set -e
APP="$HOME/.config/today"
AGENTS="$HOME/Library/LaunchAgents"
LABEL="com.today.scheduler"
PLIST="$LABEL.plist"
# labels used by earlier installs, booted out so a rename does not leave two login items running
LEGACY_LABELS="com.today.scheduler"

chmod +x "$HOME/.local/bin/today" "$APP/today.py" 2>/dev/null || true

# ~/.local/bin on PATH for zsh (uv's env file already does this if you sourced it)
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) grep -q '.local/bin' "$HOME/.zshrc" 2>/dev/null || printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.zshrc"
     echo "added ~/.local/bin to PATH in ~/.zshrc (open a new terminal)";;
esac

# login item: open Terminal with today's plan. The plist is generated from the template so that no
# absolute home path is committed to the repository.
mkdir -p "$AGENTS"
sed "s|__APP_DIR__|$APP|g" "$APP/$PLIST.template" > "$AGENTS/$PLIST"

for old in $LEGACY_LABELS; do
  launchctl bootout "gui/$(id -u)/$old" 2>/dev/null || true
  rm -f "$AGENTS/$old.plist"
done
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AGENTS/$PLIST"
echo "login item installed: $AGENTS/$PLIST"
echo "  it just fired once to test — a Terminal window with today's plan should be opening."
echo "  (macOS may ask to allow osascript to control Terminal: click OK.)"
echo
echo "remove later with:  launchctl bootout gui/\$(id -u)/$LABEL; rm ~/Library/LaunchAgents/$PLIST"
