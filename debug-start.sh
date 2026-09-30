#!/bin/sh
# Starts the Today window from this Terminal so anything that goes wrong prints here
# (and in ~/.config/today/logs/gui.log). Close the Today window, or press Ctrl+C here, to stop.
#   sh ~/.config/today/debug-start.sh
pkill -f "config/today/today_gui.py" 2>/dev/null && sleep 1
cd "$HOME/.config/today" || exit 1
echo "Today is starting from this Terminal. Keep this window open; errors will show up below."
echo
exec ./venv/bin/python -u today_gui.py
