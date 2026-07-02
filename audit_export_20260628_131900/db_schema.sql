===== DB SCHEMA =====
CREATE TABLE candles(
  symbol TEXT, interval TEXT, open_time INTEGER, close_time INTEGER,
  open REAL, high REAL, low REAL, close REAL, volume REAL, quote_volume REAL,
  trades INTEGER, taker_buy_base REAL, taker_buy_quote REAL,
  PRIMARY KEY(symbol, interval, open_time)
);
CREATE TABLE market_snapshots(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, price REAL, payload TEXT
);
CREATE TABLE sqlite_sequence(name,seq);
CREATE TABLE derivatives_snapshots(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, funding REAL, open_interest REAL,
  oi_chg_5m REAL, oi_chg_1h REAL, long_short REAL, top_long_short REAL, taker_buy_ratio REAL, basis_bps REAL, payload TEXT
);
CREATE TABLE orderflow_snapshots(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, spread_bps REAL, depth_10bps REAL,
  depth_25bps REAL, imbalance_25bps REAL, wall_pressure REAL, cvd_proxy REAL, payload TEXT
);
CREATE TABLE macro_snapshots(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, risk_score REAL, mode TEXT, payload TEXT
);
CREATE TABLE news_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, source TEXT, category TEXT, severity REAL, direction TEXT, title TEXT, url TEXT, payload TEXT
, hash TEXT);
CREATE TABLE features(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, regime TEXT, session TEXT, volatility_bucket TEXT,
  news_bucket TEXT, data_quality REAL, payload TEXT
);
CREATE TABLE decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, mode TEXT, symbol TEXT, action TEXT, side TEXT, setup TEXT,
  final_score REAL, confidence REAL, size_usd REAL, payload TEXT
);
CREATE TABLE positions(
  id TEXT PRIMARY KEY, opened_at TEXT, closed_at TEXT, symbol TEXT, side TEXT, setup TEXT,
  status TEXT, entry REAL, exit REAL, size_usd REAL, pnl_usd REAL, payload TEXT
);
CREATE TABLE position_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, position_id TEXT, event TEXT, symbol TEXT, payload TEXT
);
CREATE TABLE trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, position_id TEXT, symbol TEXT, side TEXT, setup TEXT,
  pnl_usd REAL, pnl_r REAL, fees REAL, reason TEXT, payload TEXT
);
CREATE TABLE edge_memory(
  key TEXT, source TEXT, updated_at TEXT, n REAL, wins REAL, losses REAL,
  sum_r REAL, sum_pos_r REAL, sum_neg_r REAL, max_dd_r REAL, payload TEXT,
  PRIMARY KEY(key, source)
);
CREATE TABLE forward_cases(
  id TEXT PRIMARY KEY, created_at TEXT, due_at TEXT, horizon_min INTEGER, symbol TEXT,
  side TEXT, action TEXT, setup TEXT, entry REAL, sl REAL, tp1 REAL, tp2 REAL, status TEXT, payload TEXT
);
CREATE TABLE forward_results(
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, resolved_at TEXT, symbol TEXT, outcome TEXT,
  result_r REAL, mfe_r REAL, mae_r REAL, payload TEXT
);
CREATE TABLE alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, severity TEXT, kind TEXT, symbol TEXT, dedup_key TEXT, payload TEXT
, text TEXT);
CREATE TABLE runtime_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, component TEXT, level TEXT, message TEXT, payload TEXT
);
CREATE TABLE runtime_control_audit(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                source TEXT,
                action TEXT,
                before_json TEXT,
                after_json TEXT,
                pending_json TEXT,
                note TEXT
            );
CREATE TABLE state_integrity_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                version TEXT,
                event TEXT,
                plan_hash TEXT,
                status TEXT,
                payload TEXT
            );
CREATE TABLE sqlite_stat1(tbl,idx,stat);
CREATE INDEX idx_decisions_ts ON decisions(ts);
CREATE INDEX idx_features_symbol_ts ON features(symbol, ts);
CREATE INDEX idx_trades_symbol ON trades(symbol, setup);
CREATE INDEX idx_forward_status ON forward_cases(status, due_at);
CREATE UNIQUE INDEX idx_news_events_hash ON news_events(hash);
CREATE INDEX idx_news_events_ts ON news_events(ts);
CREATE INDEX idx_alerts_dedup_key ON alerts(dedup_key);
CREATE INDEX idx_alerts_ts ON alerts(ts);
CREATE INDEX idx_alerts_dedup_kind ON alerts(dedup_key);
CREATE INDEX idx_state_integrity_ts ON state_integrity_events(ts);
CREATE INDEX idx_state_integrity_hash ON state_integrity_events(plan_hash);
CREATE TABLE liquidity_liquidation_events_v1 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                recv_ts TEXT NOT NULL,
                event_ms INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                liquidation_side TEXT NOT NULL,
                price REAL NOT NULL,
                qty REAL NOT NULL,
                usd REAL NOT NULL,
                source TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE INDEX idx_liq_events_symbol_event_ms_v1
            ON liquidity_liquidation_events_v1(symbol, event_ms);
CREATE TABLE liquidity_features_v1 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                source TEXT NOT NULL,
                version TEXT NOT NULL,
                lookback_min INTEGER NOT NULL,
                data_status TEXT NOT NULL,
                ref_price REAL,

                event_count INTEGER NOT NULL,
                buy_liq_usd REAL NOT NULL,
                sell_liq_usd REAL NOT NULL,
                total_liq_usd REAL NOT NULL,
                net_liq_usd REAL NOT NULL,
                imbalance REAL NOT NULL,

                decayed_buy_liq_usd REAL NOT NULL,
                decayed_sell_liq_usd REAL NOT NULL,
                decayed_imbalance REAL NOT NULL,

                short_squeeze_pressure REAL NOT NULL,
                long_flush_pressure REAL NOT NULL,
                stress_score REAL NOT NULL,

                max_event_usd REAL NOT NULL,
                p95_event_usd REAL NOT NULL,
                latest_event_age_sec REAL,

                dominant_side TEXT NOT NULL,
                dominant_bucket_bps INTEGER,
                dominant_bucket_usd REAL NOT NULL,

                nearest_above_bucket_bps INTEGER,
                nearest_above_usd REAL NOT NULL,
                nearest_below_bucket_bps INTEGER,
                nearest_below_usd REAL NOT NULL,

                source_health TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE INDEX idx_liq_features_symbol_id_v1
            ON liquidity_features_v1(symbol, id);
CREATE TABLE liquidity_source_health_v1 (
                source TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT NOT NULL,
                latency_ms REAL
            );
CREATE TABLE bayesian_evidence_scores_v1 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                setup TEXT NOT NULL,

                forward_n INTEGER NOT NULL,
                forward_exp_r REAL NOT NULL,
                forward_pf REAL,
                forward_winrate REAL NOT NULL,
                forward_avg_mfe_r REAL NOT NULL,
                forward_avg_mae_r REAL NOT NULL,

                clean_exec_n INTEGER NOT NULL,
                clean_exec_exp_usd REAL NOT NULL,
                clean_exec_pnl_usd REAL NOT NULL,
                clean_exec_winrate REAL NOT NULL,

                excluded_exec_n INTEGER NOT NULL,
                excluded_pnl_usd REAL NOT NULL,

                effective_n REAL NOT NULL,
                raw_combined_exp REAL NOT NULL,
                shrunk_exp_r REAL NOT NULL,
                confidence REAL NOT NULL,
                robustness_score REAL NOT NULL,
                divergence_penalty REAL NOT NULL,
                quality_score REAL NOT NULL,

                status TEXT NOT NULL,
                allow_open INTEGER NOT NULL,
                allow_probe INTEGER NOT NULL,
                size_multiplier_cap REAL NOT NULL,

                reasons TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE INDEX idx_bayesian_evidence_scores_v1_key
            ON bayesian_evidence_scores_v1(symbol, side, setup, id);
CREATE TABLE outcome_provenance_v1 (
                position_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                setup TEXT NOT NULL,
                opened_at TEXT,
                closed_at TEXT,
                status TEXT,
                pnl_usd REAL,
                provenance TEXT NOT NULL,
                clean_for_evidence INTEGER NOT NULL,
                evidence_weight REAL NOT NULL,
                exclude_reason TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE INDEX idx_outcome_provenance_v1_key
            ON outcome_provenance_v1(symbol, side, setup, clean_for_evidence);
CREATE TABLE evidence_hygiene_summary_v1 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                setup TEXT NOT NULL,
                total_closed_positions INTEGER NOT NULL,
                clean_closed_positions INTEGER NOT NULL,
                excluded_positions INTEGER NOT NULL,
                clean_pnl_usd REAL NOT NULL,
                excluded_pnl_usd REAL NOT NULL,
                payload TEXT NOT NULL
            );
CREATE TABLE evidence_registry_audit_v1 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE TABLE research_promotion_decisions_v1 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                setup TEXT NOT NULL,

                source_status TEXT NOT NULL,
                forward_n INTEGER NOT NULL,
                forward_exp_r REAL NOT NULL,
                forward_pf REAL,
                clean_exec_n INTEGER NOT NULL,
                excluded_exec_n INTEGER NOT NULL,
                shrunk_exp_r REAL NOT NULL,
                divergence_penalty REAL NOT NULL,
                quality_score REAL NOT NULL,

                allow_canary_probe INTEGER NOT NULL,
                allow_direct_open INTEGER NOT NULL,
                size_multiplier_cap REAL NOT NULL,
                absolute_size_usd_cap REAL NOT NULL,

                promotion_state TEXT NOT NULL,
                reasons TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE INDEX idx_research_promotion_decisions_v1_key
            ON research_promotion_decisions_v1(symbol, side, setup, id);
CREATE TABLE alpha_research_features_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                price REAL NOT NULL,

                regime TEXT NOT NULL,
                session TEXT NOT NULL,
                volatility_bucket TEXT NOT NULL,
                data_quality_score REAL NOT NULL,

                score_15m REAL NOT NULL,
                score_1h REAL NOT NULL,
                score_4h REAL NOT NULL,
                rsi_15m REAL NOT NULL,
                rsi_1h REAL NOT NULL,
                rsi_4h REAL NOT NULL,
                atr_abs_1h REAL NOT NULL,
                atr_pct_1h REAL NOT NULL,

                liquidity_score REAL NOT NULL,
                derivatives_score REAL NOT NULL,
                funding_rate REAL NOT NULL,
                open_interest REAL NOT NULL,
                long_short_ratio REAL NOT NULL,
                squeeze_risk REAL NOT NULL,
                macro_risk REAL NOT NULL,
                news_severity REAL NOT NULL,

                btc_eth_rel_strength REAL NOT NULL,
                context_ok INTEGER NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,

                UNIQUE(symbol, ts)
            );
CREATE INDEX idx_alpha_research_features_v2_symbol_ts
            ON alpha_research_features_v2(symbol, ts);
CREATE TABLE alpha_triple_barrier_labels_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feature_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                profile TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                exit_ts TEXT NOT NULL,
                exit_price REAL NOT NULL,
                tp_r REAL NOT NULL,
                sl_r REAL NOT NULL,

                result_r REAL NOT NULL,
                mfe_r REAL NOT NULL,
                mae_r REAL NOT NULL,
                hit_type TEXT NOT NULL,
                bars_seen INTEGER NOT NULL,

                label_quality TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,

                UNIQUE(symbol, ts, side, profile)
            );
CREATE INDEX idx_alpha_triple_barrier_labels_v2_key
            ON alpha_triple_barrier_labels_v2(symbol, side, profile, ts);
CREATE TABLE alpha_research_edges_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                edge_key TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                profile TEXT NOT NULL,
                segment_type TEXT NOT NULL,
                segment_value TEXT NOT NULL,

                n INTEGER NOT NULL,
                expectancy_r REAL NOT NULL,
                shrunk_expectancy_r REAL NOT NULL,
                winrate REAL NOT NULL,
                winrate_lcb REAL NOT NULL,
                profit_factor REAL,
                avg_mfe_r REAL NOT NULL,
                avg_mae_r REAL NOT NULL,
                max_dd_r REAL NOT NULL,
                train_exp_r REAL NOT NULL,
                validation_exp_r REAL NOT NULL,
                stability_score REAL NOT NULL,

                quality_score REAL NOT NULL,
                state TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                reasons TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE INDEX idx_alpha_research_edges_v2_key
            ON alpha_research_edges_v2(edge_key, id);
CREATE TABLE alpha_realtime_scores_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                score REAL NOT NULL,
                expected_r REAL NOT NULL,
                confidence REAL NOT NULL,
                matched_edges INTEGER NOT NULL,
                best_profile TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                reasons TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE INDEX idx_alpha_realtime_scores_v2_key
            ON alpha_realtime_scores_v2(symbol, side, id);
CREATE TABLE alpha_research_audit_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE VIEW latest_alpha_research_edges_v2 AS
            SELECT e.*
            FROM alpha_research_edges_v2 e
            JOIN (
                SELECT edge_key, MAX(id) AS max_id
                FROM alpha_research_edges_v2
                GROUP BY edge_key
            ) x ON x.max_id = e.id
/* latest_alpha_research_edges_v2(id,ts,version,edge_key,symbol,side,profile,segment_type,segment_value,n,expectancy_r,shrunk_expectancy_r,winrate,winrate_lcb,profit_factor,avg_mfe_r,avg_mae_r,max_dd_r,train_exp_r,validation_exp_r,stability_score,quality_score,state,recommendation,reasons,payload) */;
CREATE VIEW latest_alpha_realtime_scores_v2 AS
            SELECT s.*
            FROM alpha_realtime_scores_v2 s
            JOIN (
                SELECT symbol, side, MAX(id) AS max_id
                FROM alpha_realtime_scores_v2
                GROUP BY symbol, side
            ) x ON x.max_id = s.id
/* latest_alpha_realtime_scores_v2(id,ts,version,symbol,side,score,expected_r,confidence,matched_edges,best_profile,recommendation,reasons,payload) */;
CREATE TABLE universal_shadow_cases_v2 (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                due_at TEXT NOT NULL,
                resolved_at TEXT,
                status TEXT NOT NULL,

                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                setup TEXT NOT NULL,
                profile TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,

                entry REAL NOT NULL,
                sl REAL NOT NULL,
                tp1 REAL NOT NULL,
                tp2 REAL NOT NULL,
                risk_abs REAL NOT NULL,

                context_bucket TEXT NOT NULL,
                context_score REAL NOT NULL,
                thesis TEXT NOT NULL,
                counter_thesis TEXT NOT NULL,
                invalidation TEXT NOT NULL,

                payload TEXT NOT NULL
            );
CREATE TABLE universal_shadow_results_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                resolved_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                setup TEXT NOT NULL,
                profile TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,

                outcome TEXT NOT NULL,
                result_r REAL NOT NULL,
                mfe_r REAL NOT NULL,
                mae_r REAL NOT NULL,
                bars_seen INTEGER NOT NULL,
                exit_price REAL NOT NULL,

                payload TEXT NOT NULL
            );
CREATE TABLE universal_shadow_registry_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                alpha_key TEXT NOT NULL,

                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                setup TEXT NOT NULL,
                profile TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,
                context_bucket TEXT NOT NULL,

                n INTEGER NOT NULL,
                expectancy_r REAL NOT NULL,
                winrate REAL NOT NULL,
                profit_factor REAL,
                avg_mfe_r REAL NOT NULL,
                avg_mae_r REAL NOT NULL,
                train_exp_r REAL NOT NULL,
                validation_exp_r REAL NOT NULL,
                stability_score REAL NOT NULL,
                quality_score REAL NOT NULL,

                state TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                reasons TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE TABLE universal_shadow_alpha_audit_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE INDEX idx_universal_shadow_cases_v2_status_due
            ON universal_shadow_cases_v2(status, due_at);
CREATE INDEX idx_universal_shadow_registry_v2_key
            ON universal_shadow_registry_v2(alpha_key, id);
CREATE TABLE alpha_evidence_tensor_v5 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,

                alpha_key TEXT NOT NULL,
                cluster_key TEXT NOT NULL,

                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                setup TEXT NOT NULL,
                profile TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,

                learned_context_bucket TEXT NOT NULL,
                current_context_bucket TEXT NOT NULL,
                current_context_fit REAL NOT NULL,

                n INTEGER NOT NULL,
                n_recent INTEGER NOT NULL,
                n_older INTEGER NOT NULL,

                mean_r REAL NOT NULL,
                median_r REAL NOT NULL,
                std_r REAL NOT NULL,
                winrate REAL NOT NULL,
                profit_factor REAL NOT NULL,
                profit_factor_capped REAL NOT NULL,

                expectancy_r REAL NOT NULL,
                shrunk_expectancy_r REAL NOT NULL,
                lcb_expectancy_r REAL NOT NULL,

                train_exp_r REAL NOT NULL,
                validation_exp_r REAL NOT NULL,
                recent_exp_r REAL NOT NULL,
                older_exp_r REAL NOT NULL,

                p05_r REAL NOT NULL,
                p10_r REAL NOT NULL,
                worst_r REAL NOT NULL,
                best_r REAL NOT NULL,

                avg_mfe_r REAL NOT NULL,
                avg_mae_r REAL NOT NULL,
                mfe_mae_efficiency REAL NOT NULL,

                fold_1_r REAL NOT NULL,
                fold_2_r REAL NOT NULL,
                fold_3_r REAL NOT NULL,
                fold_4_r REAL NOT NULL,
                fold_positive_n INTEGER NOT NULL,
                fold_min_r REAL NOT NULL,
                fold_pass INTEGER NOT NULL,

                decay_slope REAL NOT NULL,
                decay_state TEXT NOT NULL,
                tail_risk_state TEXT NOT NULL,

                sample_quality REAL NOT NULL,
                path_quality REAL NOT NULL,
                stability_quality REAL NOT NULL,
                context_quality REAL NOT NULL,
                tensor_quality REAL NOT NULL,

                raw_cases_json TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE INDEX idx_alpha_evidence_tensor_v5_alpha
            ON alpha_evidence_tensor_v5(alpha_key, id);
CREATE INDEX idx_alpha_evidence_tensor_v5_cluster
            ON alpha_evidence_tensor_v5(cluster_key, id);
CREATE TABLE alpha_evidence_tensor_audit_v5 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE TABLE alpha_bayesian_posterior_v5 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,

                alpha_key TEXT NOT NULL,
                cluster_key TEXT NOT NULL,

                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                setup TEXT NOT NULL,
                profile TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,

                learned_context_bucket TEXT NOT NULL,
                current_context_bucket TEXT NOT NULL,
                current_context_fit REAL NOT NULL,

                n INTEGER NOT NULL,
                effective_n REAL NOT NULL,

                tensor_expectancy_r REAL NOT NULL,
                tensor_shrunk_r REAL NOT NULL,
                tensor_lcb_r REAL NOT NULL,
                tensor_validation_r REAL NOT NULL,
                tensor_recent_r REAL NOT NULL,
                tensor_older_r REAL NOT NULL,

                tensor_std_r REAL NOT NULL,
                tensor_pf_cap REAL NOT NULL,
                tensor_quality REAL NOT NULL,

                posterior_mean_r REAL NOT NULL,
                posterior_std_r REAL NOT NULL,
                posterior_lcb_r REAL NOT NULL,
                posterior_ucb_r REAL NOT NULL,

                prob_edge_gt_zero REAL NOT NULL,
                prob_edge_gt_min REAL NOT NULL,
                prob_loss_gt_025r REAL NOT NULL,
                prob_loss_gt_050r REAL NOT NULL,
                prob_tail_event REAL NOT NULL,

                sample_quality REAL NOT NULL,
                validation_quality REAL NOT NULL,
                context_quality REAL NOT NULL,
                decay_quality REAL NOT NULL,
                tail_quality REAL NOT NULL,
                fold_quality REAL NOT NULL,
                posterior_quality REAL NOT NULL,

                posterior_score_raw REAL NOT NULL,
                posterior_score REAL NOT NULL,

                posterior_state TEXT NOT NULL,
                allowed_meta_governance INTEGER NOT NULL,
                allowed_direct_open INTEGER NOT NULL,

                recommended_next_action TEXT NOT NULL,
                reasons TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE INDEX idx_alpha_bayesian_posterior_v5_alpha
            ON alpha_bayesian_posterior_v5(alpha_key, id);
CREATE INDEX idx_alpha_bayesian_posterior_v5_cluster
            ON alpha_bayesian_posterior_v5(cluster_key, id);
CREATE TABLE alpha_bayesian_posterior_audit_v5 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE TABLE alpha_meta_governance_v5 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,

                alpha_key TEXT NOT NULL,
                cluster_key TEXT NOT NULL,

                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                setup TEXT NOT NULL,
                profile TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,

                learned_context_bucket TEXT NOT NULL,
                current_context_bucket TEXT NOT NULL,
                current_context_fit REAL NOT NULL,

                n INTEGER NOT NULL,
                effective_n REAL NOT NULL,

                posterior_mean_r REAL NOT NULL,
                posterior_lcb_r REAL NOT NULL,
                posterior_ucb_r REAL NOT NULL,
                posterior_score REAL NOT NULL,

                prob_edge_gt_zero REAL NOT NULL,
                prob_edge_gt_min REAL NOT NULL,
                prob_loss_gt_025r REAL NOT NULL,
                prob_loss_gt_050r REAL NOT NULL,
                prob_tail_event REAL NOT NULL,

                tensor_quality REAL NOT NULL,
                tensor_validation_r REAL NOT NULL,
                tensor_lcb_r REAL NOT NULL,

                cluster_rank INTEGER NOT NULL,
                is_cluster_leader INTEGER NOT NULL,
                cluster_size INTEGER NOT NULL,
                duplicate_penalty REAL NOT NULL,

                edge_quality REAL NOT NULL,
                probability_quality REAL NOT NULL,
                safety_quality REAL NOT NULL,
                context_quality REAL NOT NULL,
                sample_quality REAL NOT NULL,
                posterior_quality REAL NOT NULL,
                cluster_quality REAL NOT NULL,

                meta_score_raw REAL NOT NULL,
                meta_score REAL NOT NULL,

                meta_state TEXT NOT NULL,
                allowed_promotion_contract INTEGER NOT NULL,
                allowed_direct_open INTEGER NOT NULL,

                size_cap_usd REAL NOT NULL,
                max_daily_per_alpha INTEGER NOT NULL,
                max_daily_global INTEGER NOT NULL,

                recommendation TEXT NOT NULL,
                next_requirement TEXT NOT NULL,
                reasons TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE INDEX idx_alpha_meta_governance_v5_alpha
            ON alpha_meta_governance_v5(alpha_key, id);
CREATE INDEX idx_alpha_meta_governance_v5_cluster
            ON alpha_meta_governance_v5(cluster_key, id);
CREATE TABLE alpha_meta_governance_audit_v5 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE TABLE alpha_promotion_contract_v5 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,

                contract_id TEXT NOT NULL,
                alpha_key TEXT NOT NULL,
                cluster_key TEXT NOT NULL,

                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                setup TEXT NOT NULL,
                profile TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,

                learned_context_bucket TEXT NOT NULL,
                current_context_bucket TEXT NOT NULL,
                current_context_fit REAL NOT NULL,

                valid_from TEXT NOT NULL,
                expires_at TEXT NOT NULL,

                n INTEGER NOT NULL,
                effective_n REAL NOT NULL,

                meta_score REAL NOT NULL,
                posterior_score REAL NOT NULL,
                posterior_mean_r REAL NOT NULL,
                posterior_lcb_r REAL NOT NULL,

                prob_edge_gt_zero REAL NOT NULL,
                prob_edge_gt_min REAL NOT NULL,
                prob_loss_gt_025r REAL NOT NULL,
                prob_tail_event REAL NOT NULL,

                tensor_quality REAL NOT NULL,
                tensor_validation_r REAL NOT NULL,

                cluster_rank INTEGER NOT NULL,
                is_cluster_leader INTEGER NOT NULL,
                cluster_size INTEGER NOT NULL,

                allowed_paper_micro_canary INTEGER NOT NULL,
                allowed_direct_open INTEGER NOT NULL,
                required_execution_mode TEXT NOT NULL,

                size_cap_usd REAL NOT NULL,
                size_multiplier_cap REAL NOT NULL,
                max_daily_per_alpha INTEGER NOT NULL,
                max_daily_global INTEGER NOT NULL,

                contract_state TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                next_requirement TEXT NOT NULL,
                reasons TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE INDEX idx_alpha_promotion_contract_v5_alpha
            ON alpha_promotion_contract_v5(alpha_key, id);
CREATE INDEX idx_alpha_promotion_contract_v5_contract
            ON alpha_promotion_contract_v5(contract_id, id);
CREATE TABLE alpha_promotion_contract_audit_v5 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE TABLE institutional_control_plane_v6 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,

                global_state TEXT NOT NULL,
                control_score REAL NOT NULL,

                allow_standard_open INTEGER NOT NULL,
                allow_direct_open INTEGER NOT NULL,
                allow_paper_micro_canary INTEGER NOT NULL,
                force_learning_only INTEGER NOT NULL,
                veto_new_positions INTEGER NOT NULL,

                max_size_usd REAL NOT NULL,
                max_daily_new_positions INTEGER NOT NULL,
                allowed_symbols TEXT NOT NULL,
                allowed_sides TEXT NOT NULL,

                required_execution_mode TEXT NOT NULL,
                recommended_action TEXT NOT NULL,
                next_required_build TEXT NOT NULL,

                contracts_n INTEGER NOT NULL,
                contract_state TEXT NOT NULL,
                micro_canary_ready INTEGER NOT NULL,

                max_meta_score REAL NOT NULL,
                max_posterior_score REAL NOT NULL,
                max_posterior_mean_r REAL NOT NULL,
                max_posterior_lcb_r REAL NOT NULL,
                max_prob_edge_gt_zero REAL NOT NULL,
                max_prob_edge_gt_min REAL NOT NULL,
                max_prob_loss REAL NOT NULL,
                max_prob_tail REAL NOT NULL,
                max_tensor_quality REAL NOT NULL,

                shadow_100_avg_r REAL NOT NULL,
                shadow_100_winrate REAL NOT NULL,
                shadow_300_avg_r REAL NOT NULL,
                shadow_300_winrate REAL NOT NULL,
                shadow_600_avg_r REAL NOT NULL,
                shadow_600_winrate REAL NOT NULL,

                best_family_symbol TEXT,
                best_family_side TEXT,
                best_family_name TEXT,
                best_family_horizon_min INTEGER NOT NULL,
                best_family_n INTEGER NOT NULL,
                best_family_avg_r REAL NOT NULL,
                best_family_winrate REAL NOT NULL,
                best_family_worst_r REAL NOT NULL,
                best_family_best_r REAL NOT NULL,

                last_trade_ts TEXT,
                last10_pnl_usd REAL NOT NULL,
                last25_pnl_usd REAL NOT NULL,
                last10_winrate_usd REAL NOT NULL,
                last25_winrate_usd REAL NOT NULL,
                open_positions INTEGER NOT NULL,

                alpha_runtime_errors INTEGER NOT NULL,
                hard_vetoes TEXT NOT NULL,
                reasons TEXT NOT NULL,
                control_contract_json TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE INDEX idx_institutional_control_plane_v6_id
            ON institutional_control_plane_v6(id);
CREATE TABLE institutional_control_plane_audit_v6 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE VIEW evidence_clean_positions_v1 AS
            SELECT p.*
            FROM positions p
            JOIN outcome_provenance_v1 op
              ON op.position_id = p.id
            WHERE op.clean_for_evidence = 1
/* evidence_clean_positions_v1(id,opened_at,closed_at,symbol,side,setup,status,entry,exit,size_usd,pnl_usd,payload) */;
CREATE VIEW evidence_clean_trades_v1 AS
            SELECT t.*
            FROM trades t
            JOIN outcome_provenance_v1 op
              ON op.position_id = t.position_id
            WHERE op.clean_for_evidence = 1
/* evidence_clean_trades_v1(id,ts,position_id,symbol,side,setup,pnl_usd,pnl_r,fees,reason,payload) */;
CREATE VIEW evidence_positions_with_provenance_v1 AS
            SELECT
                p.*,
                op.provenance,
                op.clean_for_evidence,
                op.evidence_weight,
                op.exclude_reason
            FROM positions p
            LEFT JOIN outcome_provenance_v1 op
              ON op.position_id = p.id
/* evidence_positions_with_provenance_v1(id,opened_at,closed_at,symbol,side,setup,status,entry,exit,size_usd,pnl_usd,payload,provenance,clean_for_evidence,evidence_weight,exclude_reason) */;
CREATE VIEW latest_bayesian_evidence_v1 AS
            SELECT s.*
            FROM bayesian_evidence_scores_v1 s
            JOIN (
                SELECT symbol, side, setup, MAX(id) AS max_id
                FROM bayesian_evidence_scores_v1
                GROUP BY symbol, side, setup
            ) x
              ON x.max_id = s.id
/* latest_bayesian_evidence_v1(id,ts,version,symbol,side,setup,forward_n,forward_exp_r,forward_pf,forward_winrate,forward_avg_mfe_r,forward_avg_mae_r,clean_exec_n,clean_exec_exp_usd,clean_exec_pnl_usd,clean_exec_winrate,excluded_exec_n,excluded_pnl_usd,effective_n,raw_combined_exp,shrunk_exp_r,confidence,robustness_score,divergence_penalty,quality_score,status,allow_open,allow_probe,size_multiplier_cap,reasons,payload) */;
CREATE VIEW latest_research_promotion_v1 AS
            SELECT p.*
            FROM research_promotion_decisions_v1 p
            JOIN (
                SELECT symbol, side, setup, MAX(id) AS max_id
                FROM research_promotion_decisions_v1
                GROUP BY symbol, side, setup
            ) x ON x.max_id = p.id
/* latest_research_promotion_v1(id,ts,version,symbol,side,setup,source_status,forward_n,forward_exp_r,forward_pf,clean_exec_n,excluded_exec_n,shrunk_exp_r,divergence_penalty,quality_score,allow_canary_probe,allow_direct_open,size_multiplier_cap,absolute_size_usd_cap,promotion_state,reasons,payload) */;
CREATE TABLE alpha_cluster_aggregator_v6 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                refresh_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,

                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                family_name TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,

                n INTEGER NOT NULL,
                avg_r REAL NOT NULL,
                winrate REAL NOT NULL,
                worst_r REAL NOT NULL,
                best_r REAL NOT NULL,
                std_r REAL NOT NULL,
                lcb_r REAL NOT NULL,
                ucb_r REAL NOT NULL,

                sample_quality REAL NOT NULL,
                edge_quality REAL NOT NULL,
                stability_quality REAL NOT NULL,
                cluster_score REAL NOT NULL,

                cluster_state TEXT NOT NULL,
                hard_vetoes TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE TABLE alpha_cluster_aggregator_audit_v6 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE TABLE regime_adaptive_router_v6 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,

                regime_state TEXT NOT NULL,
                regime_score REAL NOT NULL,

                selected_symbol TEXT,
                selected_side TEXT,
                selected_family TEXT,
                selected_horizon_min INTEGER NOT NULL,

                cluster_n INTEGER NOT NULL,
                cluster_avg_r REAL NOT NULL,
                cluster_lcb_r REAL NOT NULL,
                cluster_winrate REAL NOT NULL,
                cluster_score REAL NOT NULL,
                cluster_state TEXT NOT NULL,

                shadow_100_avg_r REAL NOT NULL,
                shadow_300_avg_r REAL NOT NULL,
                shadow_600_avg_r REAL NOT NULL,

                allow_cluster_review INTEGER NOT NULL,
                allow_micro_canary_candidate INTEGER NOT NULL,
                hard_vetoes TEXT NOT NULL,
                reasons TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE TABLE institutional_control_plane_v7 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,

                global_state TEXT NOT NULL,
                control_score REAL NOT NULL,

                allow_standard_open INTEGER NOT NULL,
                allow_direct_open INTEGER NOT NULL,
                allow_paper_micro_canary INTEGER NOT NULL,
                micro_canary_candidate INTEGER NOT NULL,
                force_learning_only INTEGER NOT NULL,
                veto_new_positions INTEGER NOT NULL,

                recommended_action TEXT NOT NULL,
                next_required_build TEXT NOT NULL,
                required_execution_mode TEXT NOT NULL,

                cluster_symbol TEXT,
                cluster_side TEXT,
                cluster_family TEXT,
                cluster_horizon_min INTEGER NOT NULL,
                cluster_n INTEGER NOT NULL,
                cluster_avg_r REAL NOT NULL,
                cluster_lcb_r REAL NOT NULL,
                cluster_winrate REAL NOT NULL,
                cluster_score REAL NOT NULL,
                cluster_state TEXT NOT NULL,

                regime_state TEXT NOT NULL,
                regime_score REAL NOT NULL,

                contracts_n INTEGER NOT NULL,
                micro_contract_ready INTEGER NOT NULL,
                max_contract_lcb REAL NOT NULL,
                max_tensor_quality REAL NOT NULL,

                open_positions INTEGER NOT NULL,
                last_trade_ts TEXT,
                last10_pnl_usd REAL NOT NULL,

                hard_vetoes TEXT NOT NULL,
                reasons TEXT NOT NULL,
                control_contract_json TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE VIEW latest_institutional_control_plane_v7 AS
            SELECT *
            FROM institutional_control_plane_v7
            ORDER BY id DESC
            LIMIT 1
/* latest_institutional_control_plane_v7(id,ts,version,global_state,control_score,allow_standard_open,allow_direct_open,allow_paper_micro_canary,micro_canary_candidate,force_learning_only,veto_new_positions,recommended_action,next_required_build,required_execution_mode,cluster_symbol,cluster_side,cluster_family,cluster_horizon_min,cluster_n,cluster_avg_r,cluster_lcb_r,cluster_winrate,cluster_score,cluster_state,regime_state,regime_score,contracts_n,micro_contract_ready,max_contract_lcb,max_tensor_quality,open_positions,last_trade_ts,last10_pnl_usd,hard_vetoes,reasons,control_contract_json,payload) */;
CREATE TABLE institutional_edge_factory_v8 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                refresh_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,

                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                family_name TEXT NOT NULL,
                setup TEXT NOT NULL,
                profile TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,

                n INTEGER NOT NULL,
                avg_r REAL NOT NULL,
                winrate REAL NOT NULL,
                worst_r REAL NOT NULL,
                best_r REAL NOT NULL,
                std_r REAL NOT NULL,
                lcb_r REAL NOT NULL,
                ucb_r REAL NOT NULL,

                recent_n INTEGER NOT NULL,
                recent_avg_r REAL NOT NULL,
                older_avg_r REAL NOT NULL,
                decay_r REAL NOT NULL,

                tail_loss_rate REAL NOT NULL,
                positive_tail_rate REAL NOT NULL,

                sample_quality REAL NOT NULL,
                edge_quality REAL NOT NULL,
                recency_quality REAL NOT NULL,
                tail_quality REAL NOT NULL,
                edge_score REAL NOT NULL,

                edge_state TEXT NOT NULL,
                micro_canary_eligible INTEGER NOT NULL,

                hard_vetoes TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE TABLE institutional_edge_factory_audit_v8 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE TABLE edge_robustness_validator_v9 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                refresh_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,

                source_edge_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                family_name TEXT NOT NULL,
                setup TEXT NOT NULL,
                profile TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,

                n INTEGER NOT NULL,
                avg_r REAL NOT NULL,
                lcb_r REAL NOT NULL,
                winrate REAL NOT NULL,
                worst_r REAL NOT NULL,
                best_r REAL NOT NULL,

                recent20_n INTEGER NOT NULL,
                recent20_avg_r REAL NOT NULL,
                recent20_lcb_r REAL NOT NULL,
                recent20_winrate REAL NOT NULL,

                recent50_n INTEGER NOT NULL,
                recent50_avg_r REAL NOT NULL,
                recent50_lcb_r REAL NOT NULL,
                recent50_winrate REAL NOT NULL,

                decay_guard REAL NOT NULL,
                overfit_penalty REAL NOT NULL,
                robustness_score REAL NOT NULL,

                validation_state TEXT NOT NULL,
                canary_permission INTEGER NOT NULL,

                hard_vetoes TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE TABLE micro_canary_outcome_feedback_v9 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,

                closed_n INTEGER NOT NULL,
                last5_n INTEGER NOT NULL,
                last5_avg_r REAL NOT NULL,
                last5_sum_r REAL NOT NULL,
                last5_winrate REAL NOT NULL,
                loss_streak INTEGER NOT NULL,

                feedback_state TEXT NOT NULL,
                canary_cooldown INTEGER NOT NULL,
                hard_vetoes TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE TABLE institutional_control_plane_v9 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,

                global_state TEXT NOT NULL,
                control_score REAL NOT NULL,

                allow_standard_open INTEGER NOT NULL,
                allow_direct_open INTEGER NOT NULL,
                allow_paper_micro_canary INTEGER NOT NULL,
                force_learning_only INTEGER NOT NULL,
                veto_new_positions INTEGER NOT NULL,

                max_size_usd REAL NOT NULL,
                max_daily_canaries INTEGER NOT NULL,
                required_execution_mode TEXT NOT NULL,

                recommended_action TEXT NOT NULL,
                next_required_build TEXT NOT NULL,

                source_edge_id INTEGER NOT NULL,
                edge_symbol TEXT,
                edge_side TEXT,
                edge_family TEXT,
                edge_setup TEXT,
                edge_profile TEXT,
                edge_horizon_min INTEGER NOT NULL,
                edge_n INTEGER NOT NULL,
                edge_avg_r REAL NOT NULL,
                edge_lcb_r REAL NOT NULL,
                edge_winrate REAL NOT NULL,
                robustness_score REAL NOT NULL,
                validation_state TEXT NOT NULL,

                regime_state TEXT NOT NULL,
                regime_score REAL NOT NULL,
                feedback_state TEXT NOT NULL,

                open_legacy_positions INTEGER NOT NULL,
                open_canaries INTEGER NOT NULL,
                today_canaries INTEGER NOT NULL,

                hard_vetoes TEXT NOT NULL,
                reasons TEXT NOT NULL,
                control_contract_json TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE TABLE paper_micro_canary_positions_v9 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                family_name TEXT NOT NULL,
                setup TEXT NOT NULL,
                profile TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,
                status TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                size_usd REAL NOT NULL,
                pnl_usd REAL NOT NULL,
                pnl_r REAL NOT NULL,
                control_id INTEGER NOT NULL,
                source_edge_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE TABLE paper_micro_canary_audit_v9 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
CREATE VIEW latest_universal_shadow_registry_v2 AS
            SELECT r.*
            FROM universal_shadow_registry_v2 r
            JOIN (
                SELECT alpha_key, MAX(id) AS max_id
                FROM universal_shadow_registry_v2
                GROUP BY alpha_key
            ) x ON x.max_id = r.id
/* latest_universal_shadow_registry_v2(id,ts,version,alpha_key,symbol,side,setup,profile,horizon_min,context_bucket,n,expectancy_r,winrate,profit_factor,avg_mfe_r,avg_mae_r,train_exp_r,validation_exp_r,stability_score,quality_score,state,recommendation,reasons,payload) */;
CREATE VIEW latest_alpha_evidence_tensor_v5 AS
            SELECT t.*
            FROM alpha_evidence_tensor_v5 t
            JOIN (
                SELECT alpha_key, MAX(id) AS max_id
                FROM alpha_evidence_tensor_v5
                GROUP BY alpha_key
            ) x ON x.max_id = t.id
/* latest_alpha_evidence_tensor_v5(id,ts,version,alpha_key,cluster_key,symbol,side,setup,profile,horizon_min,learned_context_bucket,current_context_bucket,current_context_fit,n,n_recent,n_older,mean_r,median_r,std_r,winrate,profit_factor,profit_factor_capped,expectancy_r,shrunk_expectancy_r,lcb_expectancy_r,train_exp_r,validation_exp_r,recent_exp_r,older_exp_r,p05_r,p10_r,worst_r,best_r,avg_mfe_r,avg_mae_r,mfe_mae_efficiency,fold_1_r,fold_2_r,fold_3_r,fold_4_r,fold_positive_n,fold_min_r,fold_pass,decay_slope,decay_state,tail_risk_state,sample_quality,path_quality,stability_quality,context_quality,tensor_quality,raw_cases_json,payload) */;
CREATE VIEW latest_alpha_bayesian_posterior_v5 AS
            SELECT p.*
            FROM alpha_bayesian_posterior_v5 p
            JOIN (
                SELECT alpha_key, MAX(id) AS max_id
                FROM alpha_bayesian_posterior_v5
                GROUP BY alpha_key
            ) x ON x.max_id = p.id
/* latest_alpha_bayesian_posterior_v5(id,ts,version,alpha_key,cluster_key,symbol,side,setup,profile,horizon_min,learned_context_bucket,current_context_bucket,current_context_fit,n,effective_n,tensor_expectancy_r,tensor_shrunk_r,tensor_lcb_r,tensor_validation_r,tensor_recent_r,tensor_older_r,tensor_std_r,tensor_pf_cap,tensor_quality,posterior_mean_r,posterior_std_r,posterior_lcb_r,posterior_ucb_r,prob_edge_gt_zero,prob_edge_gt_min,prob_loss_gt_025r,prob_loss_gt_050r,prob_tail_event,sample_quality,validation_quality,context_quality,decay_quality,tail_quality,fold_quality,posterior_quality,posterior_score_raw,posterior_score,posterior_state,allowed_meta_governance,allowed_direct_open,recommended_next_action,reasons,payload) */;
CREATE VIEW latest_alpha_meta_governance_v5 AS
            SELECT m.*
            FROM alpha_meta_governance_v5 m
            JOIN (
                SELECT alpha_key, MAX(id) AS max_id
                FROM alpha_meta_governance_v5
                GROUP BY alpha_key
            ) x ON x.max_id = m.id
/* latest_alpha_meta_governance_v5(id,ts,version,alpha_key,cluster_key,symbol,side,setup,profile,horizon_min,learned_context_bucket,current_context_bucket,current_context_fit,n,effective_n,posterior_mean_r,posterior_lcb_r,posterior_ucb_r,posterior_score,prob_edge_gt_zero,prob_edge_gt_min,prob_loss_gt_025r,prob_loss_gt_050r,prob_tail_event,tensor_quality,tensor_validation_r,tensor_lcb_r,cluster_rank,is_cluster_leader,cluster_size,duplicate_penalty,edge_quality,probability_quality,safety_quality,context_quality,sample_quality,posterior_quality,cluster_quality,meta_score_raw,meta_score,meta_state,allowed_promotion_contract,allowed_direct_open,size_cap_usd,max_daily_per_alpha,max_daily_global,recommendation,next_requirement,reasons,payload) */;
CREATE VIEW latest_alpha_promotion_contract_v5 AS
            SELECT c.*
            FROM alpha_promotion_contract_v5 c
            JOIN (
                SELECT alpha_key, MAX(id) AS max_id
                FROM alpha_promotion_contract_v5
                GROUP BY alpha_key
            ) x ON x.max_id = c.id
/* latest_alpha_promotion_contract_v5(id,ts,version,contract_id,alpha_key,cluster_key,symbol,side,setup,profile,horizon_min,learned_context_bucket,current_context_bucket,current_context_fit,valid_from,expires_at,n,effective_n,meta_score,posterior_score,posterior_mean_r,posterior_lcb_r,prob_edge_gt_zero,prob_edge_gt_min,prob_loss_gt_025r,prob_tail_event,tensor_quality,tensor_validation_r,cluster_rank,is_cluster_leader,cluster_size,allowed_paper_micro_canary,allowed_direct_open,required_execution_mode,size_cap_usd,size_multiplier_cap,max_daily_per_alpha,max_daily_global,contract_state,recommendation,next_requirement,reasons,payload) */;
CREATE VIEW latest_institutional_control_plane_v6 AS
            SELECT *
            FROM institutional_control_plane_v6
            ORDER BY id DESC
            LIMIT 1
/* latest_institutional_control_plane_v6(id,ts,version,global_state,control_score,allow_standard_open,allow_direct_open,allow_paper_micro_canary,force_learning_only,veto_new_positions,max_size_usd,max_daily_new_positions,allowed_symbols,allowed_sides,required_execution_mode,recommended_action,next_required_build,contracts_n,contract_state,micro_canary_ready,max_meta_score,max_posterior_score,max_posterior_mean_r,max_posterior_lcb_r,max_prob_edge_gt_zero,max_prob_edge_gt_min,max_prob_loss,max_prob_tail,max_tensor_quality,shadow_100_avg_r,shadow_100_winrate,shadow_300_avg_r,shadow_300_winrate,shadow_600_avg_r,shadow_600_winrate,best_family_symbol,best_family_side,best_family_name,best_family_horizon_min,best_family_n,best_family_avg_r,best_family_winrate,best_family_worst_r,best_family_best_r,last_trade_ts,last10_pnl_usd,last25_pnl_usd,last10_winrate_usd,last25_winrate_usd,open_positions,alpha_runtime_errors,hard_vetoes,reasons,control_contract_json,payload) */;
CREATE VIEW latest_alpha_cluster_aggregator_v6 AS
            SELECT *
            FROM alpha_cluster_aggregator_v6
            WHERE refresh_id = (
                SELECT MAX(refresh_id)
                FROM alpha_cluster_aggregator_v6
            )
/* latest_alpha_cluster_aggregator_v6(id,refresh_id,ts,version,symbol,side,family_name,horizon_min,n,avg_r,winrate,worst_r,best_r,std_r,lcb_r,ucb_r,sample_quality,edge_quality,stability_quality,cluster_score,cluster_state,hard_vetoes,payload) */;
CREATE VIEW latest_institutional_edge_factory_v8 AS
            SELECT *
            FROM institutional_edge_factory_v8
            WHERE refresh_id = (
                SELECT MAX(refresh_id)
                FROM institutional_edge_factory_v8
            )
/* latest_institutional_edge_factory_v8(id,refresh_id,ts,version,symbol,side,family_name,setup,profile,horizon_min,n,avg_r,winrate,worst_r,best_r,std_r,lcb_r,ucb_r,recent_n,recent_avg_r,older_avg_r,decay_r,tail_loss_rate,positive_tail_rate,sample_quality,edge_quality,recency_quality,tail_quality,edge_score,edge_state,micro_canary_eligible,hard_vetoes,payload) */;
CREATE VIEW latest_edge_robustness_validator_v9 AS
            SELECT *
            FROM edge_robustness_validator_v9
            WHERE refresh_id = (
                SELECT MAX(refresh_id)
                FROM edge_robustness_validator_v9
            )
/* latest_edge_robustness_validator_v9(id,refresh_id,ts,version,source_edge_id,symbol,side,family_name,setup,profile,horizon_min,n,avg_r,lcb_r,winrate,worst_r,best_r,recent20_n,recent20_avg_r,recent20_lcb_r,recent20_winrate,recent50_n,recent50_avg_r,recent50_lcb_r,recent50_winrate,decay_guard,overfit_penalty,robustness_score,validation_state,canary_permission,hard_vetoes,payload) */;
CREATE VIEW latest_regime_adaptive_router_v6 AS
            SELECT *
            FROM regime_adaptive_router_v6
            ORDER BY id DESC
            LIMIT 1
/* latest_regime_adaptive_router_v6(id,ts,version,regime_state,regime_score,selected_symbol,selected_side,selected_family,selected_horizon_min,cluster_n,cluster_avg_r,cluster_lcb_r,cluster_winrate,cluster_score,cluster_state,shadow_100_avg_r,shadow_300_avg_r,shadow_600_avg_r,allow_cluster_review,allow_micro_canary_candidate,hard_vetoes,reasons,payload) */;
CREATE VIEW latest_micro_canary_outcome_feedback_v9 AS
            SELECT *
            FROM micro_canary_outcome_feedback_v9
            ORDER BY id DESC
            LIMIT 1
/* latest_micro_canary_outcome_feedback_v9(id,ts,version,closed_n,last5_n,last5_avg_r,last5_sum_r,last5_winrate,loss_streak,feedback_state,canary_cooldown,hard_vetoes,payload) */;
CREATE VIEW latest_institutional_control_plane_v9 AS
            SELECT *
            FROM institutional_control_plane_v9
            ORDER BY id DESC
            LIMIT 1
/* latest_institutional_control_plane_v9(id,ts,version,global_state,control_score,allow_standard_open,allow_direct_open,allow_paper_micro_canary,force_learning_only,veto_new_positions,max_size_usd,max_daily_canaries,required_execution_mode,recommended_action,next_required_build,source_edge_id,edge_symbol,edge_side,edge_family,edge_setup,edge_profile,edge_horizon_min,edge_n,edge_avg_r,edge_lcb_r,edge_winrate,robustness_score,validation_state,regime_state,regime_score,feedback_state,open_legacy_positions,open_canaries,today_canaries,hard_vetoes,reasons,control_contract_json,payload) */;
