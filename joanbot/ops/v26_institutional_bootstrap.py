
from __future__ import annotations

import json, traceback

INSTALLED = False

def install():
    global INSTALLED
    if INSTALLED:
        return True

    try:
        from joanbot.institutional.outcome_learning_v26 import get_core
        from joanbot.execution.broker import PaperBroker
        from joanbot.intelligence.memory import EdgeMemory
        from joanbot.intelligence.decision import DecisionKernel

        if not getattr(PaperBroker.close_position, "_v26_wrapped", False):
            _orig_close = PaperBroker.close_position

            def _v26_close_position(self, pos, exit_price, reason, close_pct=1.0):
                trade = _orig_close(self, pos, exit_price, reason, close_pct)

                try:
                    rows = self.db.query(
                        "SELECT * FROM trades WHERE position_id=? ORDER BY id DESC LIMIT 1",
                        (trade.get("position_id") if isinstance(trade, dict) else pos.get("id"),)
                    )
                    if rows:
                        tr = dict(rows[0])
                        payload = json.loads(tr.get("payload") or "{}")
                        tr.update(payload)
                        tr["id"] = rows[0].get("id")
                        tr["position_id"] = rows[0].get("position_id")
                        tr["pnl_usd"] = rows[0].get("pnl_usd")
                        tr["fees"] = rows[0].get("fees")
                        tr["reason"] = rows[0].get("reason")
                        res = get_core(self.db).record_live_close(pos, tr)
                        if isinstance(trade, dict):
                            trade["pnl_r"] = res.get("result_r")
                            trade["v26"] = res
                except Exception as e:
                    try:
                        self.db.runtime_event("v26_learning", "ERROR", "live_close_record_failed", {
                            "error": repr(e),
                            "trace": traceback.format_exc(limit=4),
                            "position_id": pos.get("id") if isinstance(pos, dict) else None,
                        })
                    except Exception:
                        pass

                return trade

            _v26_close_position._v26_wrapped = True
            PaperBroker.close_position = _v26_close_position

        if not getattr(EdgeMemory.update_many, "_v26_wrapped", False):
            _orig_update_many = EdgeMemory.update_many

            def _v26_update_many(self, keys, source, result_r, payload=None):
                try:
                    src = str(source).upper()
                    payload = payload or {}
                    if src == "FORWARD" and isinstance(payload, dict):
                        joined = "|".join(str(k) for k in keys or [])
                        if "UNKNOWN" in joined and payload.get("case_id"):
                            new_keys, new_payload = get_core(self.db).contextual_forward_keys(payload)
                            return _orig_update_many(self, new_keys, source, result_r, new_payload)
                except Exception:
                    pass
                return _orig_update_many(self, keys, source, result_r, payload)

            _v26_update_many._v26_wrapped = True
            EdgeMemory.update_many = _v26_update_many

        if not getattr(DecisionKernel.decide_for_context, "_v26_wrapped", False):
            _orig_decide = DecisionKernel.decide_for_context

            def _v26_decide_for_context(self, ctx, wallet, mode="LIVE"):
                decisions = _orig_decide(self, ctx, wallet, mode=mode)

                try:
                    core = get_core(self.edge.db if hasattr(self, "edge") else __import__("joanbot.storage", fromlist=["get_db"]).get_db())
                except Exception:
                    core = None

                if core:
                    out = []
                    for d in decisions:
                        try:
                            out.append(core.apply_training_policy_to_decision(d, wallet))
                        except Exception:
                            out.append(d)
                    return out

                return decisions

            _v26_decide_for_context._v26_wrapped = True
            DecisionKernel.decide_for_context = _v26_decide_for_context

        INSTALLED = True
        return True

    except Exception:
        return False
