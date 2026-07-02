#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from joanbot.institutional.canonical_equity_v24_5 import connect, snapshot

OUT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14/data/v24_5_canonical_equity")
OUT.mkdir(parents=True, exist_ok=True)

con = connect()
s = snapshot(con)
con.close()

OUT.joinpath("snapshot.json").write_text(json.dumps(s, indent=2, sort_keys=True))

lines = []
lines.append("# V24.5 CANONICAL EQUITY")
lines.append(f"- UTC: `{s['ts']}`")
lines.append(f"- Start equity: `{s['start_equity']:.2f}$`")
lines.append(f"- Closed net PnL: `{s['closed_net_pnl_usd']:.2f}$`")
lines.append(f"- Open floating net PnL: `{s['open_net_pnl_usd']:.2f}$`")
lines.append(f"- Total equity: `{s['total_equity']:.2f}$`")
lines.append(f"- Closed trades: `{s['closed_trades']}`")
lines.append(f"- Open positions: `{s['open_positions']}`")
lines.append(f"- Suspicious positions: `{s['suspicious_positions']}`")
lines.append("")
lines.append("## Positions")
for p in s["positions"][-10:]:
    lines.append(
        f"- id={p.get('id')} {p.get('symbol')} {p.get('side')} {p.get('setup')} "
        f"status={p.get('status')} net={p.get('db_net_pnl_usd')} r={p.get('db_net_r')} "
        f"flags={p.get('flags')}"
    )

OUT.joinpath("summary.md").write_text("\n".join(lines))

print("\n".join(lines))
