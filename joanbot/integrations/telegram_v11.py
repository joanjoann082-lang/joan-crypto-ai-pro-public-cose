from __future__ import annotations

import os
import urllib.parse
import urllib.request
from pathlib import Path


def load_env() -> None:
    p = Path(".env")
    if not p.exists():
        return
    for line in p.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def enabled() -> bool:
    load_env()
    return os.getenv("TELEGRAM_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def send_message(text: str) -> bool:
    load_env()

    if not enabled():
        return False

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode()

    with urllib.request.urlopen(url, data=data, timeout=15) as r:
        r.read()

    return True
