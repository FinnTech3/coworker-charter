#!/usr/bin/env bash
# Rebuilds docs/demo.gif, docs/demo.mp4 and the PNGs from real runs of the scripts in this repo.
# Needs: python3, ffmpeg, Pillow, node, and Playwright (set PLAYWRIGHT_DIR to a folder that has it in node_modules).
# Nothing in the images is typed by hand: record.py runs the commands and keeps what they print.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# A clean folder: the three scripts, the support charter and the two log events.
cp "$root"/agensi/coworker-charter/scripts/*.py "$work/"
cp "$root/agensi/coworker-charter/references/example-support-agent.json" "$work/charter.json"
cp "$here"/events/*.json "$work/"

sed "s|@WORK@|$work|" "$here/demo.json" > "$work/demo.spec.json"
python3 "$here/record.py" "$work/demo.spec.json" "$work/demo.cast.json"
node "$here/termcast.mjs" "$work/demo.cast.json" "$root/docs/demo.gif" "$root/docs/demo.mp4"
node "$here/termcast.mjs" "$work/demo.cast.json" "$work/demo-final.png" --still

WORK="$work" ROOT="$root" node "$here/media.mjs"
python3 "$here/finish.py" "$work" "$root"
