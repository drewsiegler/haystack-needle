#!/bin/bash
# Makes the Haystack Needle app, which opens the Subscribers page with a
# double-click:
#     packaging/macos/build_app.sh [folder]
# It puts "Haystack Needle.app" in /Applications (or ~/Applications if that
# isn't writable, or the folder given), replacing an older one.
#
# The app is the small AppleScript wrapper beside this file. It runs this
# checkout's .venv and code and holds no addresses, so it needs making again
# only if this folder moves or the wrapper changes. Made on this Mac rather
# than downloaded, it opens without Gatekeeper's questions; it's signed ad hoc,
# which macOS requires of every app.
set -euo pipefail
cd "$(dirname "$0")/../.."
REPO=$(pwd -P)
NAME="Haystack Needle"
if [ -n "${1:-}" ]; then
  DEST=$1
elif [ -w /Applications ]; then
  DEST=/Applications
else
  DEST="$HOME/Applications"
fi

if [ ! -x .venv/bin/python ]; then
  echo "There's no .venv in $REPO yet. Set it up first (README, \"Trying it\")." >&2
  exit 1
fi
if pgrep -qf "/$NAME.app/Contents/MacOS/applet"; then
  echo "Haystack Needle is open. Quit it first (right-click its Dock icon, then Quit), and try again." >&2
  exit 1
fi

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
APP="$STAGE/$NAME.app"
osacompile -s -o "$APP" "packaging/macos/$NAME.applescript" 2> /dev/null  # -s: stays open until you quit it
PLIST="$APP/Contents/Info.plist"
plutil -replace CFBundleIdentifier -string io.github.drewsiegler.haystackneedle "$PLIST"
plutil -replace CFBundleName -string "$NAME" "$PLIST"
plutil -replace HaystackNeedleFolder -string "$REPO" "$PLIST"  # where the app finds the page
# The icon: osacompile's own comes from Assets.car, which wins over applet.icns.
plutil -remove CFBundleIconName "$PLIST"
rm -f "$APP/Contents/Resources/Assets.car"
cp packaging/macos/AppIcon.icns "$APP/Contents/Resources/applet.icns"
codesign --force --sign - "$APP" 2> /dev/null

mkdir -p "$DEST"
rm -rf "${DEST:?}/$NAME.app"
ditto "$APP" "$DEST/$NAME.app"
echo "Made $DEST/$NAME.app"
