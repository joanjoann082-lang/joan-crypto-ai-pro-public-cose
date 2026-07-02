# LIVE BOT STATUS

- Updated UTC: `2026-07-02T13:46:36.544904+00:00`
- Exporter: `V25_MAX_FREE_LIVE_STATUS_EXPORTER`
- Health: `GREEN`
- DB quick_check: `ok`

## Money / Cash / PnL

| Metric | Value |
|---|---:|
| Base equity | `+100,000.00 $` |
| Cash balance realized | `+99,998.93 $` |
| Marked equity | `+99,998.93 $` |
| Realized PnL | `-1.07 $` |
| Unrealized PnL | `0.00 $` |
| Total PnL | `-1.07 $` |
| Realized return | `-0.001072 %` |
| Marked return | `-0.001072 %` |
| Open positions | `0` |
| Closed positions | `7` |
| Win rate | `57.1429 %` |
| Profit factor | `0.890143` |
| Expectancy / trade | `-0.15 $` |
| Max realized drawdown | `-7.35 $` |
| Open exposure | `0.00 $` |
| Approx open risk | `0.00 $` |

## Price

- market_health_ok: `True`
- reason: `PRICE_OK`
- BTCUSDT: ok=`True` price=`61921.87407246` source=`BINANCE_FAPI_PREMIUM_INDEX` age=`0.216206` reason=`CANONICAL_PRICE_OK`
- ETHUSDT: ok=`True` price=`1709.58619801` source=`BINANCE_FAPI_PREMIUM_INDEX` age=`0.210494` reason=`CANONICAL_PRICE_OK`

## Adapter
- id=2298 quick=ok pending=0 opened=0 managed=0 closed=0 rejected=0 errors=0
- id=2297 quick=ok pending=0 opened=0 managed=0 closed=0 rejected=0 errors=0
- id=2296 quick=ok pending=0 opened=0 managed=0 closed=0 rejected=0 errors=0

## Processes
- runtime_manager: `1`
- price_contract: `2`
- quant_authority: `2`
- canonical_adapter: `2`
- canonical_equity: `1`
- live_status_sync: `1`
- old_v17_adapter: `0`
- old_v23: `0`

## Latest positions
- id=7 ETHUSDT SHORT UAL2_BOUNCE_FADE_SHORT status=CLOSED net=2.4009749999999714 r=1.0670999999999873 reason=OPENED_CANONICAL_PAPER_CANARY
- id=6 ETHUSDT SHORT UAL2_BOUNCE_FADE_SHORT status=CLOSED net=-3.251349999999941 r=-1.445044444444423 reason=STOP_LOSS_HIT
- id=5 ETHUSDT SHORT UAL2_BOUNCE_FADE_SHORT status=CLOSED net=-3.251349999999933 r=-1.4450444444444375 reason=STOP_LOSS_HIT
- id=4 ETHUSDT SHORT UAL2_BOUNCE_FADE_SHORT status=CLOSED net=2.40097499999995 r=1.0670999999999842 reason=STOP_LOSS_HIT
- id=3 ETHUSDT SHORT UAL2_SQUEEZE_REVERSAL_SHORT status=CLOSED net=-3.2513499999999906 r=-1.4450444444444297 reason=STOP_LOSS_HIT
- id=2 BTCUSDT LONG UAL2_SQUEEZE_REVERSAL_LONG status=CLOSED net=3.688803019084254 r=1.5149399450026713 reason=V11_CANARY_TAKE_PROFIT_NET_1_5R
- id=1 BTCUSDT SHORT UAL2_SQUEEZE_REVERSAL_SHORT status=CLOSED net=0.19175087415841444 r=0.6391695805280482 reason=V11_CANARY_HORIZON_CLOSE

## Latest intents
- id=119 state=ADAPTER_BOUND_OPEN_PAPER_CANARY adapter=OPENED_PAPER_CANARY_BY_V24_4_CANONICAL_ADAPTER ETHUSDT SHORT UAL2_BOUNCE_FADE_SHORT
- id=118 state=ADAPTER_BOUND_OPEN_PAPER_CANARY adapter=OPENED_PAPER_CANARY_BY_V24_4_CANONICAL_ADAPTER ETHUSDT SHORT UAL2_BOUNCE_FADE_SHORT
- id=117 state=ADAPTER_REJECTED adapter=REJECTED_BY_V24_4_CANONICAL_ADAPTER ETHUSDT SHORT UAL2_SQUEEZE_REVERSAL_SHORT
- id=116 state=ADAPTER_BOUND_OPEN_PAPER_CANARY adapter=OPENED_PAPER_CANARY_BY_V24_4_CANONICAL_ADAPTER ETHUSDT SHORT UAL2_BOUNCE_FADE_SHORT
- id=115 state=ADAPTER_BOUND_OPEN_PAPER_CANARY adapter=OPENED_PAPER_CANARY_BY_V24_4_CANONICAL_ADAPTER ETHUSDT SHORT UAL2_BOUNCE_FADE_SHORT
- id=114 state=ADAPTER_ERROR_QUARANTINED adapter=ERROR_QUARANTINED_BY_V24_6D_CANONICAL_ADAPTER ETHUSDT SHORT UAL2_SQUEEZE_REVERSAL_SHORT
- id=113 state=ADAPTER_REJECTED adapter=REJECTED_BY_V24_4_CANONICAL_ADAPTER ETHUSDT SHORT UAL2_BOUNCE_FADE_SHORT
- id=112 state=ADAPTER_BOUND_OPEN_PAPER_CANARY adapter=OPENED_PAPER_CANARY_BY_V17_8_1 ETHUSDT SHORT UAL2_SQUEEZE_REVERSAL_SHORT
- id=111 state=ADAPTER_REJECTED adapter=REJECTED_BY_V17_8_1 ETHUSDT SHORT UAL2_BOUNCE_FADE_SHORT
- id=110 state=ADAPTER_REJECTED adapter=REJECTED_BY_V17_8_1 ETHUSDT SHORT UAL2_SQUEEZE_REVERSAL_SHORT

## Recent error summary

Raw logs are not published. Only sanitized counts.
- traceback: `0`
- database_locked: `0`
- abort: `0`
- fatal: `0`
- exception: `0`
- integrity: `0`

## Health problems
- `NONE`