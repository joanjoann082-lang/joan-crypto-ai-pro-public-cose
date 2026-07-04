from pathlib import Path
import sys, json
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from joanbot.storage import get_db
from joanbot.institutional.nucli_quantitatiu_net import get_core


def main():
    db = get_db()
    core = get_core(db)
    pos = {
        "id": "PROVA_NUCLI_QUANT_NET_POS",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "setup": "PROVA_NUCLI_QUANT_NET",
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "initial_stop_loss": 95.0,
        "size_usd": 10000.0,
        "opened_at": "2026-01-01T00:00:00+00:00",
        "meta": {"decision": {"feature_summary": {"regime": "TEST", "session": "TEST", "volatility_bucket": "TEST", "news_bucket": "TEST"}}},
    }
    trade = {
        "id": 999999777,
        "position_id": "PROVA_NUCLI_QUANT_NET_POS",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "setup": "PROVA_NUCLI_QUANT_NET",
        "entry": 100.0,
        "exit": 105.0,
        "size_usd": 10000.0,
        "pnl_usd": 500.0,
        "fees": 0.0,
        "reason": "SELF_TEST",
        "ts": "2026-01-01T01:00:00+00:00",
        "payload": "{}",
    }
    res = core.registra_operacio_live(pos, trade)
    core.reconstrueix_tot()
    exclusions = db.query("SELECT COUNT(*) c FROM exclusions_qualitat_dades WHERE font='LIVE' AND font_id='999999777'")[0]["c"]
    polluted = db.query("SELECT COUNT(*) c FROM estat_promocio_quant WHERE key LIKE '%PROVA_NUCLI_QUANT_NET%' OR payload LIKE '%PROVA_NUCLI_QUANT_NET%'")[0]["c"]
    assert exclusions >= 1, "la prova sintètica no s'ha exclòs"
    assert polluted == 0, "la prova sintètica ha contaminat la promoció"
    print("PROVA_NUCLI_QUANTITATIU_NET_OK", json.dumps(res, sort_keys=True))


if __name__ == "__main__":
    main()
