#!/usr/bin/env bash
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1

echo "=== START V24 CLEAN ==="
# Stop known duplicate bot processes from previous experiments. This is intentional process hygiene.
pkill -f "python.*-m core.runner" 2>/dev/null || true
pkill -f "runner.runner" 2>/dev/null || true
pkill -f "joanbot.runtime_supervisor" 2>/dev/null || true
pkill -f "python.*-m joanbot.dashboard" 2>/dev/null || true
pkill -f "python.*-m joanbot.ops.watchdog" 2>/dev/null || true
pkill -f "ops/watchdog.sh" 2>/dev/null || true
pkill -f "python.*-m joanbot.runner" 2>/dev/null || true
pkill -f "telegram_command_bot.py" 2>/dev/null || true
pkill -f "joanbot.integrations.telegram_bot" 2>/dev/null || true
sleep 5

nohup python -u -m joanbot.runner > logs/runner.log 2> logs/runner_errors.log &
nohup python -u -m joanbot.ui.dashboard > logs/dashboard.log 2> logs/dashboard_errors.log &
nohup python -u ops/telegram_command_bot.py > logs/telegram_command_bot.log 2> logs/telegram_command_bot_errors.log &
sleep 8

ps -ef | grep -E "joanbot.runner|joanbot.ui.dashboard|telegram_command_bot" | grep -v grep || true

echo "START_V24_CLEAN_DONE"
