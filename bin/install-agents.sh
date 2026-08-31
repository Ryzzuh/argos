#!/bin/zsh
# Generate and load the three launchd agents for this checkout.
#
# The plists must carry absolute paths, so they cannot be committed with real
# ones - they are generated here from the repo's actual location instead.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
TEMPLATE="$ROOT/launchd/com.argos.plist.template"

DRY_RUN=0
UNINSTALL=0
for arg in "$@"; do
  case "$arg" in
    --dry-run)   DRY_RUN=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help)
      echo "usage: $0 [--dry-run] [--uninstall]"
      exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

# job | interval seconds | arguments passed to bin/run.sh
JOBS=(
  "watch|600|watch --within-days 14 --no-report"
  "full|10800|watch"
  "watchdog|7200|health"
)

unload_label() {
  local label="$1"
  if launchctl list | grep -q "$label"; then
    launchctl unload "$AGENTS/$label.plist" 2>/dev/null || true
    echo "  unloaded $label"
  fi
}

if (( UNINSTALL )); then
  for entry in "${JOBS[@]}"; do
    job="${entry%%|*}"
    unload_label "com.argos.$job"
    rm -f "$AGENTS/com.argos.$job.plist"
  done
  echo "uninstalled."
  exit 0
fi

[[ -f "$TEMPLATE" ]] || { echo "missing template: $TEMPLATE" >&2; exit 1; }
(( DRY_RUN )) || mkdir -p "$AGENTS" "$ROOT/data"

# Older checkouts used a username in the label. Clear them out first, or the
# machine ends up running two sets of agents that compete for the same API
# rate-limit window and throttle each other into failure.
if (( ! DRY_RUN )); then
  for job in watch full watchdog; do
    unload_label "com.rhys.argos.$job"
    unload_label "com.rhys.imaxsniper.$job"
    rm -f "$AGENTS/com.rhys.argos.$job.plist" "$AGENTS/com.rhys.imaxsniper.$job.plist"
  done
fi

for entry in "${JOBS[@]}"; do
  job="${entry%%|*}"
  rest="${entry#*|}"
  interval="${rest%%|*}"
  args="${rest#*|}"

  plist=$(TEMPLATE="$TEMPLATE" LABEL="com.argos.$job" ROOT="$ROOT" JOB="$job" \
          INTERVAL="$interval" ARGS="$args" python3 -c '
import os
tpl = open(os.environ["TEMPLATE"]).read()
args = "\n".join(f"    <string>{a}</string>" for a in os.environ["ARGS"].split())
for key in ("LABEL", "ROOT", "JOB", "INTERVAL"):
    tpl = tpl.replace("{{" + key + "}}", os.environ[key])
print(tpl.replace("{{ARGS}}", args), end="")
')

  if (( DRY_RUN )); then
    echo "--- com.argos.$job.plist ---"
    echo "$plist"
  else
    echo "$plist" > "$AGENTS/com.argos.$job.plist"
    plutil -lint "$AGENTS/com.argos.$job.plist" >/dev/null
    launchctl unload "$AGENTS/com.argos.$job.plist" 2>/dev/null || true
    launchctl load "$AGENTS/com.argos.$job.plist"
    echo "  loaded com.argos.$job (every ${interval}s)"
  fi
done

(( DRY_RUN )) && echo $'\n(dry run - nothing written or loaded)' || {
  echo
  echo "installed. check with:  launchctl list | grep argos"
}
