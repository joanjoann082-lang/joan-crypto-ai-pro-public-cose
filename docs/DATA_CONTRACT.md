# Data Contract V14

All data written to SQLite WAL:

- `candles`: normalized OHLCV.
- `market_snapshots`: per-symbol snapshot.
- `derivatives_snapshots`: funding/OI/ratios/basis.
- `orderflow_snapshots`: spread/depth/imbalance/CVD proxy/liquidations.
- `macro_snapshots`: VIX/QQQ/SPY/DXY/US10Y/F&G risk.
- `news_events`: event risk categorized.
- `features`: computed context.
- `decisions`: final decisions.
- `positions`, `trades`: paper broker.
- `forward_cases`, `forward_results`: outcome validation.
- `edge_memory`: memory by source LIVE/FORWARD/SHADOW/BACKTEST.
