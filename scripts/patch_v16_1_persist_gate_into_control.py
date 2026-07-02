from pathlib import Path

p = Path("joanbot/control/control_plane_v11.py")
if not p.exists():
    raise SystemExit("CONTROL_PLANE_NOT_FOUND")

s = p.read_text()
backup = p.with_suffix(".py.before_v16_1_persist_gate")
backup.write_text(s)

marker = "ALPHA_GATE_V16_PERSISTED_IN_CONTROL_V16_1"

if marker in s:
    print("V16_1_PERSIST_GATE_ALREADY_PATCHED")
    raise SystemExit(0)

target = "INSERT INTO institutional_control_plane_v11"
pos = s.find(target)
if pos < 0:
    p.write_text(backup.read_text())
    raise SystemExit("INSERT_INTO_CONTROL_TABLE_NOT_FOUND_ROLLBACK")

# Busquem l'inici de la línia self.db.execute immediatament abans de l'INSERT.
exec_pos = s.rfind("self.db.execute", 0, pos)
if exec_pos < 0:
    p.write_text(backup.read_text())
    raise SystemExit("CONTROL_INSERT_EXECUTE_NOT_FOUND_ROLLBACK")

line_start = s.rfind("\n", 0, exec_pos) + 1

snippet = r'''
        # ALPHA_GATE_V16_PERSISTED_IN_CONTROL_V16_1
        # Important: això passa ABANS del INSERT, per tant queda persistit a SQLite.
        try:
            alpha_gate_v16 = self.q1("""
                SELECT
                    ts, version, gate_state, policy, allow_trade, size_mult,
                    selected_symbol, selected_side, selected_setup, selected_profile,
                    selected_horizon_min, hard_vetoes
                FROM latest_alpha_final_gate_v16
                LIMIT 1;
            """)
        except Exception as _alpha_e:
            alpha_gate_v16 = {}
            try:
                payload["alpha_gate_v16_error"] = repr(_alpha_e)
            except Exception:
                pass

        if alpha_gate_v16:
            try:
                import json as _alpha_json

                _alpha_state = str(alpha_gate_v16.get("gate_state") or "")
                _alpha_policy = str(alpha_gate_v16.get("policy") or "")
                _alpha_allow = int(alpha_gate_v16.get("allow_trade") or 0)
                _alpha_size_mult = float(alpha_gate_v16.get("size_mult") or 0.0)

                try:
                    _alpha_vetoes = _alpha_json.loads(alpha_gate_v16.get("hard_vetoes") or "[]")
                    if not isinstance(_alpha_vetoes, list):
                        _alpha_vetoes = [str(_alpha_vetoes)]
                except Exception:
                    _alpha_vetoes = [str(alpha_gate_v16.get("hard_vetoes") or "")]

                payload["alpha_gate_v16_seen"] = True
                payload["alpha_gate_v16_ts"] = alpha_gate_v16.get("ts")
                payload["alpha_gate_v16_state"] = _alpha_state
                payload["alpha_gate_v16_policy"] = _alpha_policy
                payload["alpha_gate_v16_allow_trade"] = _alpha_allow
                payload["alpha_gate_v16_size_mult"] = _alpha_size_mult
                payload["alpha_gate_v16_selected_symbol"] = alpha_gate_v16.get("selected_symbol")
                payload["alpha_gate_v16_selected_side"] = alpha_gate_v16.get("selected_side")
                payload["alpha_gate_v16_selected_setup"] = alpha_gate_v16.get("selected_setup")
                payload["alpha_gate_v16_selected_profile"] = alpha_gate_v16.get("selected_profile")
                payload["alpha_gate_v16_selected_horizon_min"] = alpha_gate_v16.get("selected_horizon_min")
                payload["alpha_gate_v16_hard_vetoes"] = _alpha_vetoes

                contract["alpha_gate_v16_seen"] = True
                contract["alpha_gate_v16_state"] = _alpha_state
                contract["alpha_gate_v16_policy"] = _alpha_policy
                contract["alpha_gate_v16_allow_trade"] = _alpha_allow
                contract["alpha_gate_v16_size_mult"] = _alpha_size_mult
                contract["alpha_gate_v16_hard_vetoes"] = _alpha_vetoes

                if _alpha_allow != 1:
                    allow_micro = 0
                    max_size = 0.0
                    recommended_action = "BLOCKED_BY_ALPHA_FINAL_GATE_V16"

                    if "ALPHA_FINAL_GATE_V16_BLOCK" not in hard_vetoes:
                        hard_vetoes.append("ALPHA_FINAL_GATE_V16_BLOCK")

                    for _v in _alpha_vetoes:
                        _vv = str(_v)
                        if _vv and _vv not in hard_vetoes:
                            hard_vetoes.append(_vv)

                else:
                    # V16 pot reduir, però mai pot forçar un allow si V11 ja bloqueja.
                    if _alpha_state in ("FINAL_REDUCE", "FINAL_DISCOVERY_MICRO"):
                        try:
                            max_size = float(max_size or 0.0) * min(_alpha_size_mult, 0.25)
                        except Exception:
                            max_size = 0.0

                contract["allow_paper_micro_canary"] = allow_micro
                contract["max_size_usd"] = max_size
                contract["recommended_action"] = recommended_action
                contract["hard_vetoes"] = hard_vetoes

                payload["allow_paper_micro_canary_after_alpha_gate_v16"] = allow_micro
                payload["max_size_usd_after_alpha_gate_v16"] = max_size
                payload["recommended_action_after_alpha_gate_v16"] = recommended_action

            except Exception as _alpha_apply_e:
                payload["alpha_gate_v16_apply_error"] = repr(_alpha_apply_e)
                allow_micro = 0
                max_size = 0.0
                recommended_action = "BLOCKED_ALPHA_GATE_V16_APPLY_ERROR"
                if "ALPHA_GATE_V16_APPLY_ERROR" not in hard_vetoes:
                    hard_vetoes.append("ALPHA_GATE_V16_APPLY_ERROR")

'''

s = s[:line_start] + snippet + s[line_start:]
p.write_text(s)

print("V16_1_PERSIST_GATE_PATCH_OK")
