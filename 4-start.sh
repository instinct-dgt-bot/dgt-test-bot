#!/bin/sh
set -eu
python health.py &
health_pid=$!
python bot.py &
bot_pid=$!
trap 'kill "$bot_pid" "$health_pid" 2>/dev/null || true; wait || true' INT TERM EXIT
wait "$bot_pid"
