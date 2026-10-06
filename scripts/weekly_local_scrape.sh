#!/bin/bash
# Refresh the local-only sources and publish them.
#
# CI cannot reach the sources registered runs_in_ci=False in src/sources.py:
# Boston Swing Central blocks GitHub's IP ranges, and Porter Square Books,
# Harvard Book Store and Aeronaut need a visible browser window. Their events
# only change when this runs on a Mac with someone logged in.
#
# Run weekly by the launchd agent com.cambridgecalendar.scrape-local (see
# docs/OPERATIONS.md); safe to run by hand. It pulls, scrapes, and commits and
# pushes data/events.json only if it changed. Pushing to main deploys.
#
#   scripts/weekly_local_scrape.sh            # the real thing
#   scripts/weekly_local_scrape.sh --dry-run  # scrape, but leave the result uncommitted
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin:$PATH"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

say() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }
notify() { osascript -e "display notification \"$1\" with title \"Cambridge Calendar\"" >/dev/null 2>&1 || true; }
fail() { say "FAILED: $1"; notify "Weekly local scrape failed: $1"; exit 1; }

say "weekly local scrape starting in $(pwd)"
[ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || fail "checkout is not on main"
# Someone's work in progress is not ours to commit or rebase over
git diff --quiet HEAD -- data/ src/ scrape_local.py || fail "uncommitted changes in data/ or src/"
git pull -q --rebase --autostash || fail "git pull failed"

mkdir -p logs
output=$(.venv/bin/python scrape_local.py 2>&1) || { echo "$output"; fail "scrape_local.py refused to write (see log)"; }
echo "$output" | grep -E "Scraped [0-9]+ events from|failed|Keeping stored|Total events"
failed=$(echo "$output" | grep -E "^.* - ERROR - Scraper .* failed" | sed -E 's/.* - ERROR - Scraper (.*) failed: .*/\1/' | paste -sd, - | sed 's/,/, /g')

if git diff --quiet -- data/events.json; then
    say "no change to data/events.json"
    [ -n "$failed" ] && fail "nothing new; these sources failed: $failed"
    exit 0
fi
if [ "$DRY_RUN" = 1 ]; then
    say "dry run: data/events.json left modified and uncommitted"
    exit 0
fi

git add data/events.json
git commit -q -m "Refresh local-only sources - $(date -u '+%Y-%m-%d %H:%M UTC')" || fail "git commit failed"
if ! git pull -q --rebase --autostash; then
    # CI pushed new data while this ran. Drop this run's commit rather than
    # leave a conflict behind; next week's run starts from a clean main.
    git rebase --abort 2>/dev/null
    git reset -q --keep HEAD~1
    git pull -q --rebase --autostash
    fail "main moved during the scrape; nothing pushed, rerun the script"
fi
git push -q || fail "git push failed; the commit is local, push it by hand"

say "pushed"
if [ -n "$failed" ]; then
    notify "Weekly local scrape pushed, but these sources failed: $failed"
else
    notify "Weekly local scrape pushed"
fi
