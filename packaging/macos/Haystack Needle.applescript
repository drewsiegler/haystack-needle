-- The Haystack Needle app: it opens the Subscribers page (python -m
-- needle.signups) in your browser, clicking its Dock icon opens the page
-- again, and quitting it (from the Dock, or with Command-Q) stops the page
-- after any change that's under way.
--
-- It holds no code and no addresses. It runs the .venv and code in the
-- haystack-needle folder that build_app.sh made it from (HaystackNeedleFolder
-- in its Info.plist), so changes you pull there take effect the next time it
-- opens. What the page prints goes to ~/Library/Logs/Haystack Needle.log (the
-- Console app shows it); it never prints an address.

global repo, pageURL, logFile, serverPid

on run
	set repo to do shell script "plutil -extract HaystackNeedleFolder raw -o - " & quoted form of (POSIX path of (path to me) & "Contents/Info.plist")
	set pageURL to "http://127.0.0.1:8738/"
	set logFile to POSIX path of (path to library folder from user domain) & "Logs/Haystack Needle.log"
	set serverPid to ""
	try
		do shell script "test -x " & quoted form of (repo & "/.venv/bin/python")
	on error
		display alert "Haystack Needle can't open its folder" message "It runs the page from " & repo & ", which needs its .venv (the README's \"Trying it\" says how). If that folder has moved, double-click \"Make the Haystack Needle app.command\" in it to make the app again." & return & return & "If macOS asked whether Haystack Needle may use your Documents folder and you said no, turn it on in System Settings > Privacy & Security > Files & Folders." as critical
		quit
		return
	end try
	-- The page watches this app's process ($PPID here), so it stops even if the
	-- app is force-quit. $! is the page's own process, since exec replaces the shell.
	set serverPid to do shell script "export PYTHONUTF8=1; cd " & quoted form of repo & " && exec ./.venv/bin/python -m needle.signups --app $PPID < /dev/null > " & quoted form of logFile & " 2>&1 & echo $!"
end run

on idle
	if serverPid is "" then return 2
	try
		do shell script "kill -0 " & serverPid
	on error
		-- The page stopped by itself: it was already open (and the page is up), or
		-- it couldn't start, in which case it said why after "Problem: ".
		set serverPid to ""
		set problem to do shell script "sed -n '/^Problem: /,$p' " & quoted form of logFile & " 2> /dev/null | sed '1s/^Problem: //'"
		if problem is "" and (do shell script "grep -q '^Traceback' " & quoted form of logFile & " && echo yes || true") is "yes" then
			set problem to "The page stopped because of an error. What it said is in Haystack Needle.log, which the Console app shows under Log Reports."
		end if
		if problem is not "" then display alert "Haystack Needle couldn't open the page" message problem as critical
		quit
	end try
	return 2
end idle

on reopen
	if serverPid is not "" then do shell script "open " & quoted form of pageURL
end reopen

on quit
	if serverPid is not "" then
		-- The page finishes a change first. If that takes longer than ten seconds,
		-- the app goes anyway and the page stops once the change is done.
		try
			do shell script "kill " & serverPid & "; for i in $(seq 40); do kill -0 " & serverPid & " 2> /dev/null || exit 0; sleep 0.25; done"
		end try
	end if
	continue quit
end quit
