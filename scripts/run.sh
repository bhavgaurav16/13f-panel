#!/usr/bin/env bash
# Builds every missing quarter (or START..END if given), committing after each so a timeout loses nothing.
set -euo pipefail
QS=$(python pipeline/panel.py --plan --start "${START:-}" --end "${END:-}")
echo "Quarters to build: ${QS:-none}"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
for Q in $QS; do
  python pipeline/panel.py --quarter "$Q"
  python pipeline/checks.py "$Q"
  git add data
  git commit -q -m "panel: $Q" || true
  git pull -q --rebase && git push -q
done
# dashboard flows for the latest 4 quarters (feeds the 13F Flow Monitor)
if [ -n "$QS" ] || [ ! -f data/dashboard/history.json ]; then
  mkdir -p data/dashboard
  HIST=""; [ -f data/dashboard/history.json ] && [ -z "${START:-}" ] && HIST="--history data/dashboard/history.json"
  THIRTEENF_OUT=data/dashboard THIRTEENF_CACHE=cache python pipeline/thirteenf.py --quarters 4 $HIST
  git add data/dashboard && git commit -q -m "dashboard history" || true
  git pull -q --rebase && git push -q
fi
# whole-history audit: commits its report, then fails the run (red X) if any guardrail is breached
set +e
python pipeline/audit.py; RC=$?
set -e
git add data/audit.json && git commit -q -m "audit" || true
git pull -q --rebase && git push -q
exit $RC
