-- Opens a Terminal window running `today`. Run at login by ~/Library/LaunchAgents/com.today.scheduler.plist.
-- Terminal opens an empty window when it launches; reuse that one instead of opening a second.
set cmd to "clear; python3 ~/.config/today/today.py"
tell application "System Events" to set wasRunning to (name of processes) contains "Terminal"
tell application "Terminal"
	activate
	if wasRunning then
		do script cmd
	else
		delay 0.6
		if (count of windows) > 0 then
			do script cmd in window 1
		else
			do script cmd
		end if
	end if
end tell
