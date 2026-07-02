# Architecture V14

No solapaments: una peça per responsabilitat.

- MarketDataHub: REST/macro/news/liquidations fallback.
- FeatureStore DB: guarda raw i features.
- ContextEngine: data quality + technical + levels + micro/derivatives/macro/news.
- StrategyEngine: candidats long/short. No decideix ni dimensiona.
- EdgeMemory: memòria jeràrquica per font.
- RiskEngine: size/risk budget/exposició.
- DecisionKernel: única porta OPEN/PROBE/WAIT.
- PaperBroker: execució paper.
- ProfitGuard: gestió de posició oberta.
- ForwardTester: resol decisions i alimenta memòria.
- AlertEngine: avisos deduplicats.
