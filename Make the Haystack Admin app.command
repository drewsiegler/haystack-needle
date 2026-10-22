#!/bin/bash
# Double-click this file in Finder to make the Haystack Admin app (or make it
# again), which opens the Subscribers page with a double-click. It goes in
# Applications.
cd "$(dirname "$0")" || exit 1
echo "Making the Haystack Admin app..."
if packaging/macos/build_app.sh; then
  echo
  echo "Done. Open Haystack Admin from your Applications folder. To keep it in the"
  echo "Dock, drag it there, or right-click its Dock icon while it's open and choose"
  echo "Options > Keep in Dock."
else
  echo
  echo "The app wasn't made; the reason is above."
fi
echo
read -r -p "Press Return to close this window."
