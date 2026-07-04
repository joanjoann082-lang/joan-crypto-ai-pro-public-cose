from pathlib import Path
import shutil, datetime, sys

ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
TS = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
BK = ROOT / "backups" / f"preu_canonic_fresc_{TS}"
BK.mkdir(parents=True, exist_ok=True)

FILES = [
    ROOT / "joanbot/institutional/canonical_market_data_contract_v24_9_final.py",
    ROOT / "joanbot/institutional/canonical_paper_accounting_v24_4.py",
]

def backup(p):
    dst = BK / p.relative_to(ROOT)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, dst)

for p in FILES:
    if not p.exists():
        print("ERROR: falta fitxer", p)
        sys.exit(1)
    backup(p)

# 1) Patch contracte mercat: snapshot fresc abans de cache
p = ROOT / "joanbot/institutional/canonical_market_data_contract_v24_9_final.py"
txt = p.read_text(encoding="utf-8")

patch = r'''

# === PREU_CANONIC_FRESC_V1 ===
# Regla permanent:
# canonical_price_snapshot no pot retornar cache vell si evaluate_symbol pot obtenir preu fresc.
try:
    _canonical_price_snapshot_cache_original = canonical_price_snapshot
except Exception:
    _canonical_price_snapshot_cache_original = None

def _preu_canonic_num(x, default=0.0):
    try:
        if x is None:
            return default
        y = float(x)
        if y != y:
            return default
        return y
    except Exception:
        return default

def _preu_canonic_write_status_dynamic(con, symbol, r):
    import json, datetime
    ts = r.get("ts") or datetime.datetime.now(datetime.timezone.utc).isoformat()
    source_ts = r.get("source_ts") or r.get("ts") or ts
    price = _preu_canonic_num(r.get("price") or r.get("mark_price") or r.get("markPrice") or r.get("raw_price"))
    payload = json.dumps(r, sort_keys=True, default=str)

    data = {
        "symbol": symbol,
        "ts": ts,
        "version": r.get("version") or "V24_9_2_MAX_INSTITUTIONAL_MARKET_DATA_CONTRACT",
        "ok": 1 if r.get("ok", True) else 0,
        "accepted": 1 if r.get("accepted", True) else 0,
        "price": price,
        "mark_price": _preu_canonic_num(r.get("mark_price") or r.get("markPrice") or price),
        "index_price": _preu_canonic_num(r.get("index_price") or r.get("indexPrice")),
        "reason": r.get("reason") or "CANONICAL_PRICE_OK",
        "source": r.get("source") or "BINANCE_FAPI_PREMIUM_INDEX",
        "source_ts": source_ts,
        "source_age_min": _preu_canonic_num(r.get("source_age_min"), 0.0),
        "source_col": r.get("source_col") or "markPrice",
        "source_table": r.get("source_table") or "BINANCE_FAPI_PREMIUM_INDEX",
        "confidence": _preu_canonic_num(r.get("confidence"), 1.0),
        "payload": payload,
    }

    for table in [
        "institutional_v24_9_max_market_data_status",
        "institutional_v24_9_final_market_data_status",
    ]:
        try:
            cols = [x[1] for x in con.execute(f"PRAGMA table_info({table})").fetchall()]
            if not cols or "symbol" not in cols:
                continue
            present = [c for c in cols if c in data]
            con.execute(f"DELETE FROM {table} WHERE symbol=?", (symbol,))
            qs = ",".join(["?"] * len(present))
            con.execute(
                f"INSERT INTO {table}({','.join(present)}) VALUES({qs})",
                tuple(data[c] for c in present),
            )
            con.commit()
        except Exception:
            pass

def canonical_price_snapshot(con, symbol: str):
    sym = str(symbol).upper()

    # 1. Autoritat fresca.
    try:
        r = evaluate_symbol(con, sym)
        price = _preu_canonic_num(
            r.get("price") or r.get("mark_price") or r.get("markPrice") or r.get("raw_price")
        )
        ok = bool((r.get("accepted", r.get("ok", False))) and price > 0)

        if ok:
            out = dict(r)
            out["symbol"] = sym
            out["price"] = price
            out["raw_price"] = price
            out["mark_price"] = _preu_canonic_num(out.get("mark_price") or out.get("markPrice") or price)
            out["ok"] = True
            out["reason"] = out.get("reason") or "CANONICAL_PRICE_OK"
            out["source"] = out.get("source") or "BINANCE_FAPI_PREMIUM_INDEX"
            out["confidence"] = _preu_canonic_num(out.get("confidence"), 1.0)
            _preu_canonic_write_status_dynamic(con, sym, out)
            return out
    except Exception as e:
        fresh_error = repr(e)

    # 2. Fallback cache anterior.
    if _canonical_price_snapshot_cache_original is not None:
        out = _canonical_price_snapshot_cache_original(con, sym)
        try:
            out["fresh_error"] = fresh_error
            out["stale_fallback"] = True
        except Exception:
            pass
        return out

    return {
        "symbol": sym,
        "ok": False,
        "price": None,
        "reason": "NO_FRESH_PRICE_AND_NO_CACHE",
    }
'''

if "PREU_CANONIC_FRESC_V1" not in txt:
    p.write_text(txt.rstrip() + "\n\n" + patch + "\n", encoding="utf-8")


# 2) Patch accounting: comptabilitat usa preu fresc abans del cache antic
p = ROOT / "joanbot/institutional/canonical_paper_accounting_v24_4.py"
txt = p.read_text(encoding="utf-8")

patch = r'''

# === PREU_CANONIC_FRESC_V1_ACCOUNTING ===
# Regla permanent:
# La comptabilitat paper no pot dependre d'un price table vell si el contracte V24.9 pot donar preu fresc.
try:
    _canonical_price_accounting_cache_original = canonical_price
except Exception:
    _canonical_price_accounting_cache_original = None

def _preu_accounting_num(x, default=0.0):
    try:
        if x is None:
            return default
        y = float(x)
        if y != y:
            return default
        return y
    except Exception:
        return default

def canonical_price(con, symbol: str):
    sym = str(symbol).upper()

    try:
        from joanbot.institutional.canonical_market_data_contract_v24_9_final import evaluate_symbol, canonical_price_snapshot

        for fn in (evaluate_symbol, canonical_price_snapshot):
            try:
                r = fn(con, sym)
                price = _preu_accounting_num(
                    r.get("price") or r.get("mark_price") or r.get("markPrice") or r.get("raw_price")
                )
                if price > 0 and (r.get("accepted", r.get("ok", True))):
                    meta = dict(r)
                    meta["symbol"] = sym
                    meta["price"] = price
                    meta["source"] = meta.get("source") or "BINANCE_FAPI_PREMIUM_INDEX"
                    meta["price_contract"] = "PREU_CANONIC_FRESC_V1_ACCOUNTING"
                    return price, meta
            except Exception:
                pass
    except Exception:
        pass

    if _canonical_price_accounting_cache_original is not None:
        return _canonical_price_accounting_cache_original(con, sym)

    return None, {
        "symbol": sym,
        "ok": False,
        "reason": "NO_CANONICAL_PRICE_AVAILABLE",
    }
'''

if "PREU_CANONIC_FRESC_V1_ACCOUNTING" not in txt:
    p.write_text(txt.rstrip() + "\n\n" + patch + "\n", encoding="utf-8")

print("INSTAL_LACIO_PREU_CANONIC_FRESC_OK")
print("BACKUP:", BK)
