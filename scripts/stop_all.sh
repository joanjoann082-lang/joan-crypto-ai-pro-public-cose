#!/data/data/com.termux/files/usr/bin/bash
pkill -f "joanbot.runner|joanbot.ops.watchdog|joanbot.ui.dashboard|joanbot.integrations.telegram_bot" 2>/dev/null || true
echo STOPPED
