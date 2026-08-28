#!/bin/zsh
# launchd starts jobs with a minimal PATH and no working directory, so both are
# set explicitly here. All scheduled entry points go through this wrapper.
set -u
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
exec uv run python -m "argos.$1" "${@:2}"
