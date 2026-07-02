# Joan Crypto AI PRO V14 — paper trading BTC/ETH

V14 ataca els punts febles detectats en V13: dades crítiques, FeatureStore, WebSocket/liquidacions, context, raonament per capes, sizing, backtest/replay, forward test i anti-solapaments.

## Contracte d'arquitectura

```text
MarketDataHub -> FeatureStore/DataQuality -> ContextEngine -> StrategyEngine -> EdgeMemory -> RiskEngine -> DecisionKernel -> PaperBroker/ProfitGuard -> ForwardTester -> Dashboard/Telegram/Alerts
```

Responsabilitats:
- `market/`: única font externa de dades.
- `features/`: qualitat, tècnic, VWAP, cicles, volume profile proxy, context.
- `intelligence/strategy.py`: proposa candidats. No executa.
- `intelligence/memory.py`: edge/memòria LIVE/FORWARD/SHADOW/BACKTEST.
- `intelligence/risk.py`: sizing i risk budget.
- `intelligence/decision.py`: única autoritat final OPEN/PROBE/WAIT.
- `execution/broker.py`: paper broker + ProfitGuard.
- `testing/forward.py`: forward test amb path de candles 1m.
- `testing/replay_backtester.py`: replay/backtest amb el mateix cervell.
- `ui/dashboard.py`: control room web.
- `integrations/telegram_bot.py`: consultes Telegram.

## Millores V14

- SQLite FeatureStore: candles, snapshots, orderflow, derivatives, macro, news, features, decisions, positions, trades, forward, memory.
- News/Event risk: Hormuz, guerra, petroli, Fed, CPI, FOMC, NFP, ETF, SEC, hack, exchange risk, Nasdaq risk-off.
- Dades: Binance candles multi-timeframe, orderbook, aggTrades/CVD proxy, funding, OI, L/S, top trader, taker flow, basis, liquidations fallback, macro Yahoo, Fear & Greed.
- Context: VWAP D/W/M, anchored VWAP proxy, cicles 24h/3d/7d/30d/90d/200d, POC/VAH/VAL proxy, sessions, squeeze, late long/short.
- EdgeMemory: jeràrquica i separada per font.
- RiskEngine: risk budget, stop distance, liquiditat, data quality, macro, news, exposició total/símbol/side, mida mínima útil.
- DecisionKernel: score layers alpha/timing/execution/context/edge/risk/derivatives.
- ProfitGuard: TP1 parcial, TP2, SL, break-even, profit lock, giveback.
- ForwardTest: resol decisions amb path de 1m, MFE/MAE/result R.

## Instal·lació Termux

```bash
cd /storage/emulated/0/Download
unzip joan_crypto_ai_pro_v14.zip
cd joan_crypto_ai_pro_v14
bash install_termux.sh
cp .env.example .env
```

## Validació

```bash
./scripts/smoke_test.sh
./scripts/doctor.sh
```

## Arrencar

```bash
./scripts/start_all.sh
./scripts/status.sh
```

## Dashboard

```bash
./scripts/dashboard.sh
```

Obre:

```text
http://127.0.0.1:8164
http://IP_TABLET:8164
```

## Telegram

Edita `.env`:

```env
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Arrenca:

```bash
./scripts/telegram.sh
```

Comandes: `/status`, `/positions`, `/edge`, `/why`, `/forward`, `/errors`.

## Punt honest

Això continua sent paper-only. No garanteix rendiment. La validació real és forward test 48-72h mínim, comparant amb el bot actual abans de substituir-lo.
