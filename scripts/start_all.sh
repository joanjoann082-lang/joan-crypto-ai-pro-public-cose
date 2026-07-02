#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")/.." || exit 1
export PYTHONPATH=$PWD
mkdir -p logs data backups
pkill -f "joanbot.runner" 2>/dev/null || true
nohup python -u -m joanbot.runner > logs/runner.log 2> logs/runner_errors.log &
nohup python -u -m joanbot.ops.watchdog > logs/watchdog.log 2> logs/watchdog_errors.log &
echo STARTED
pgrep -af "joanbot.runner|joanbot.ops.watchdog"
