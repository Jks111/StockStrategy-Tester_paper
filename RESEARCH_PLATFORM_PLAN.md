# Python stock strategy research platform

Prepared: 8 October 2026. Status: proposed design and research plan, not an implemented trading system.

## 1. Objective and scope

Build a local application that imports market and public insider data, tests hundreds of explicit trading hypotheses, stores reproducible results, and compares performance on unseen data. Python is the main language. Each strategy has its own file. Shared data, execution, risk and evaluation code keeps comparisons consistent.

Start with US stocks and SPY as an ETF market proxy. Use USD for trade accounting and optionally report EUR results using dated FX observations. The S&P 500 price index, SPY price returns and SPY total returns must be separately labelled. Start with backtests and local replay; connect a shortlist to Alpaca paper trading later. No real-money execution is part of this implementation plan.

Assumptions: one local user, Windows, a modest liquid-stock universe first, daily and one-minute data, and no tick-level/high-frequency execution. Hundreds of experiments means strategy families times parameter sets, universes and validation windows; those are separate trials even when they share a strategy file.

Screenshots: seven strategy/testing screenshots have now been reviewed. Their rules and limitations are recorded in section 13. The displayed performance numbers are unverified source claims; dates, instrument contracts, data, leverage and cost assumptions are missing. They do not resolve the earlier InsiderData price/filing discrepancies.

## 2. What the research supports

Research papers are sources of hypotheses and reproducible rules, not proof of future profitability. SSRN hosts working papers as well as published research; hosting alone does not establish peer review. The exact five-minute EMA rule and the insider/overnight combinations below are proposed experiments, not claimed paper replications.

| Research | Relevance | What was reviewed / limitation |
| --- | --- | --- |
| [Zarattini, Barbon and Aziz: A Profitable Day Trading Strategy for the U.S. Equity Market](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284) | Opening-range breakout and relative opening volume | Full text available through the [University of St. Gallen repository](https://alexandria.unisg.ch/server/api/core/bitstreams/3c2989c4-688d-4d78-8a71-f02690990d51/content). The baseline and volume-filtered versions differ materially; this is not the proposed EMA entry rule. |
| [Gao et al.: Intraday Momentum](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2552752) | First-half-hour return and last-half-hour trading | Search-accessible abstract reviewed. It does not validate a first-five-minute/EMA-12 signal. Full-method extraction remains a replication prerequisite. |
| [Moskowitz, Ooi and Pedersen: Time Series Momentum](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum) | Trend-following reference and baseline | Authors' research summary reviewed. The study uses futures/forwards and longer horizons; applying the idea to individual stocks or five-minute bars is an adaptation. |
| [Avellaneda and Lee: Statistical Arbitrage in the U.S. Equities Market](https://math.nyu.edu/~avellane/AvellanedaLeeStatArb20090616.pdf) | Market/sector residuals and mean reversion | Primary manuscript abstract reviewed. Residual mean reversion is a stronger research starting point than assuming every stock price returns to its moving average. Full implementation details still require extraction. |
| [Cooper, Cliff and Gulen: Like Night and Day](https://ssrn.com/abstract=1004081) and [Berkman et al.: Paying Attention](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1625495) | Close-to-open versus intraday return decomposition | Search-accessible abstracts reviewed. They motivate overnight experiments, not a conclusion that weekends or insider stocks are superior. |
| [New York Fed: The Overnight Drift](https://www.newyorkfed.org/research/staff_reports/sr917) and [The Disappearing Overnight Drift, July 2026](https://libertystreeteconomics.newyorkfed.org/2026/07/the-disappearing-overnight-drift/) | Regime dependence and post-publication failure | Primary research summary and 2026 update reviewed. The update concerns a specific 02:00-03:00 ET futures effect that faded after 2020; it is not evidence that every close-to-open stock strategy fails. |
| [Cohen, Malloy and Pomorski: Decoding Inside Information](https://ssrn.com/abstract=1692517) | Routine versus opportunistic insider activity | Search-accessible abstract reviewed. Classification must use only past filings available at each decision time. It does not establish next-morning returns after filings. |
| [Bailey et al.: The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253) | Selecting winners from hundreds of trials | Abstract reviewed. Motivates a complete trial ledger and later PBO analysis; a single held-out sample cannot fix repeated tuning against that sample. |
| [William Sharpe: CAPM prize lecture](https://www.nobelprize.org/uploads/2018/06/sharpe-lecture.pdf) and [Nobel explanatory release](https://www.nobelprize.org/prizes/economic-sciences/1990/press-release/) | Market exposure and risk-adjusted evaluation | Primary explanatory release reviewed. CAPM is an equilibrium expected-return model, not a short-term timing rule. |

### Paper-to-code workflow

For each selected paper, save bibliographic metadata, source URL, version, evidence status and our own notes in `research/papers/`. Obtain a lawful accessible full text before claiming a replication. Record the original universe, period, sampling, signal, order times, costs, corporate-action treatment, portfolio rules and reported benchmarks. Avoid reproducing the full copyrighted text in our notes or reports.

Convert the method into a rule specification and fixtures with hand-calculated expected trades. Implement the original baseline before making changes. Record deviations and unavailable inputs. Label results `replication`, `adaptation`, or `new hypothesis`. AI can draft code and identify ambiguities, but all generated modules pass the same timing and accounting checks before running. A result with a different universe or execution model is not an exact reproduction.

## 3. CAPM and additional quantitative rules

CAPM: `E[R_i] = R_f + beta_i * (E[R_m] - R_f)`.

For evaluation, fit `R_strategy,t - R_f,t = alpha + beta * (R_market,t - R_f,t) + error_t` using aligned net portfolio returns. A high return may largely be market exposure; alpha is an estimated residual performance measure, not proof of skill or a price forecast. Report estimation windows, uncertainty and benchmark definition. For strategies with changing exposure, supplement CAPM with exposure-matched benchmarks and later multifactor analysis.

Three distinct uses of regression must remain separate:

1. **Trend:** regress trailing log prices on time; use slope and fit quality as a trend feature. Price regressions can be misleading for nonstationary series; R-squared alone is not evidence of predictability.
2. **Residual mean reversion:** regress trailing stock returns on market/sector returns, build a defined residual process, and test whether deviations have historically reverted. Do not treat price-versus-time regression as a substitute for this model.
3. **Attribution:** regress strategy excess returns on benchmark excess returns to estimate alpha and beta.

All rolling coefficients used to trade are fitted from past observations only. Statistical inference should account for serial correlation and overlapping positions; use appropriate robust errors and time-block resampling. Add sector/factor models only after the single-market model works.

Shared configurable controls: liquidity eligibility from historical observations, spread/staleness limits, volatility-aware sizing, gross/net exposure limits, per-stock and sector concentration, portfolio beta bounds, max simultaneous positions, drawdown/daily-loss limits, holding deadlines and optional earnings/event exclusions. Missing earnings data means the filter is unavailable, not that no earnings event exists. Treat filters as additional research choices and include them in the trial ledger.

Initial research settings can use 0.25% equity risk per stopped trade, 5% maximum notional per stock, 10 positions and no leverage. These are editable experimental defaults, not recommended investment allocations. Overnight size uses a gap-risk assumption and not an assumption that a stop guarantees the intended loss.

## 4. Strategy catalogue: one module per strategy

Parameters below are starting hypotheses to register before testing, not optimized values or proven edges. Keep V1 simple, freeze it, then test V2 changes against V1 on the same unseen periods.

| File under `strategies/` | V1 rules | V2 / comparison |
| --- | --- | --- |
| `baselines/buy_and_hold.py` | Buy eligible universe with a fixed allocation rule; account for dividends and actions | Benchmark, not an optimized strategy |
| `baselines/overnight_market.py` | Buy SPY before close, sell next regular-session open | Separate weekday, weekend and holiday cohorts |
| `intraday/opening_ema.py` | At 09:35 New York time, compare completed 09:30-09:35 candle close with EMA-12; long above, simulated short below, flat if equal or unavailable | Compare ATR trailing exit with EMA-loss exit; additional filters live in separate V2 module |
| `intraday/opening_range_breakout.py` | Capture first five-minute high/low; take a later breakout under declared direction and risk rules | `opening_range_volume_v2.py`: add historical relative-opening-volume eligibility |
| `intraday/opening_range_15m.py` | Screenshot-derived 09:30-09:45 range; enter after a five-minute breakout close, opposite-range ATR-buffer stop, range-based target and session time exit | `opening_range_15m_v2.py`: compare insider eligibility or a volume filter separately; keep distinct from five-minute paper rule |
| `baselines/broker_daily_long.py` | Screenshot-derived broker-time daily long, pending broker calendar and instrument identification | Optional QQQ regular-session long is an adaptation, not the same strategy |
| `intraday/first_half_hour_momentum.py` | Dedicated paper-derived rule once full method is extracted | Compare independently with opening-EMA; do not substitute one for the other |
| `trend/regression_trend.py` | Positive normalized trailing log-price slope plus a declared trend-strength threshold; enter next eligible bar | `regression_trend_v2.py`: add market-regime filter, keeping the entry feature unchanged |
| `mean_reversion/price_zscore.py` | Buy a sufficiently negative trailing price deviation; exit on recovery or time limit | Simple comparison baseline; no assumption that all stocks revert |
| `mean_reversion/residual_reversion.py` | Build a past-fitted market/sector residual process, enter negative z-score, exit nearer equilibrium; optional research hedge | `residual_reversion_v2.py`: require stability/reversion evidence from past calibration window |
| `overnight/close_to_open.py` | Fixed rule formed before close; enter late in session and exit next open | `close_to_open_v2.py`: add pre-close weakness or market-regime filter, one change at a time |
| `overnight/weekend_only.py` | Friday-close to next trading-session-open, using exchange calendar | Compare per trade, per elapsed day, tails and exposure; holiday gaps analysed separately |
| `insider/post_filing_buy.py` | Buy at first eligible market opportunity after public signal availability; fixed holding horizon | Vary horizon, e.g. 1, 5 and 20 sessions, as separate trials |
| `insider/cluster_buy.py` | Buy after the kth distinct insider's qualifying purchase filing is available in a declared rolling window | Compare insider roles and relative purchase size where historical fields exist |
| `insider/opportunistic_buy.py` | Classify routine/opportunistic activity using only prior filing history; buy qualifying public signals | Paper-method extraction and sufficient past history required; do not approximate silently |
| `combined/insider_overnight_v2.py` | Fresh public insider-purchase eligibility plus close-to-open holding | Compare insider-only, overnight-only and combined cohorts |
| `combined/insider_trend_v2.py` | Insider universe plus regression-trend entry and trailing exit | Tests whether trend confirmation adds value to the insider filter |
| `combined/insider_pullback_v2.py` | Insider universe plus price/residual pullback entry | Compare rebound thesis against simple post-filing buy |
| `combined/insider_opening_ema_v2.py` | Insider eligibility known before entry plus opening-EMA signal | Long-only first; test insider sells/shorts separately, not as a symmetric mirror |
| `combined/regime_switch_v2.py` | Trailing regime classifier selects trend or reversion component | Later phase; classifier, switching costs and thresholds fitted without future data |

The earlier EUR5 profit target can remain another optional exit policy or experiment. Express its accounting currency and capital explicitly. It is not the default objective for every strategy.

### Exact specification for the opening EMA experiment

- Trading calendar/time zone: `America/New_York`, regular session. Use calendar-derived close on half-days; store timestamps in UTC and display Helsinki time when requested.
- EMA length: 12 **five-minute closes**, not 12 days or 12 one-minute bars. Primary V1 carries history across regular sessions, excludes premarket and uses a fixed documented recursive initialization and warm-up. Test premarket inclusion as a separate variant.
- Entry decision: only after the first five-minute bar is complete at 09:35. Never enter at 09:30 using its future close. Compare against EMA including that just-completed bar; a prior-bar EMA comparison is a separate trial.
- Fill: next available quote/bar event after the signal and configured latency; one-minute historical replay can use the next minute's open with explicit spread/slippage. The observed signal close is not a guaranteed fill.
- Direction: long if above, short if below, no position if equal. One entry per stock/session for V1; no automatic flip or re-entry.
- Initial stop example: 1.5 times ATR-14 calculated from available five-minute bars; position size from stop distance with notional cap.
- Trailing example: for longs, max(previous stop, highest observed price since entry minus 2 times current ATR); symmetric for shorts. Stop never loosens. Bar-derived stop updates become active on the next event/bar unless finer sequencing is available.
- Momentum-failure alternative: two consecutive completed five-minute closes on the wrong side of EMA-12. Test this as a separate exit policy before combining it with trailing exits.
- Exit: protective stop, selected trailing/momentum rule, or mandatory intraday session-end liquidation. Exit priority and gap execution are shared engine rules.
- Historical simulations may test shorts, but borrow availability, fees, recalls and short-sale execution restrictions need modelling. Run a long-only version too. Do not infer that Trading 212 Invest can execute these stock shorts.

No rule can sell at the exact instant momentum stops. The exit reacts to a measurable condition and has execution delay.

### Overnight, weekends and insider combinations

Weekends are not assumed better. Define ordinary one-session overnight holds, Friday-to-next-session holds and holiday holds separately. Report counts, costs, mean/median, downside tails, confidence intervals, elapsed holding hours, return per capital-time and contribution to total portfolio P/L. Use time-block bootstrap and control for universe, market exposure and event concentration; do not compare headline totals from cohorts with different counts.

Maintain two execution modes: a benchmark close/open decomposition using official observations, clearly labelled idealized; and an executable approximation with a fixed pre-close decision cutoff, late-session fill and next-session opening fill/slippage. If auction fills are modelled, enforce auction eligibility/order deadlines. A rule using the final daily close cannot also submit its decision earlier at that same close.

For insider overnight V2, an eligible filing must be public by the pre-close decision cutoff. An after-close filing cannot justify a purchase at that earlier close. A fresh signal can select the stock for the next eligible session. Store both public availability and our collector's first observation. Historical ideal collection and actual collector latency are separate modes.

Compare four arms on equivalent calendar periods: overnight on the base universe; simple insider post-filing holding; overnight on a randomly/matched selected comparable universe; insider-filtered overnight. Where data permit, match liquidity, sector and beta. An insider-plus-overnight benefit is accepted only if its incremental unseen performance survives costs and uncertainty. Cluster buying must become eligible only after the required filings exist, not retroactively after seeing later purchases.

## 5. Data design and reuse of InsiderData

Preserve the existing project. Build the platform in this workspace and import/export through an adapter. Its scraper can become a discovery input; its current simulations are legacy diagnostics, not trustworthy historical trade results.

Required insider fixes: retain raw rows/filings, stable transaction IDs, accession number and row identity, amendments, issuer/security identifiers, purchase/sale code, non-derivative/derivative table, role, D/I ownership, trade date, SEC acceptance/public time, source-publication time when known, first observation and fetch time. Retain rejected/unmapped rows with reasons. SEC code P includes private purchases; footnotes/security details may be needed to narrow an exchange-purchase universe. Do not classify transaction type from current-price ratios.

Replace permanent stock+insider grouping with event/window-specific grouping. Keep security/share-class mapping and symbol validity dates: HEI and HEI.A, renamed and delisted stocks must not be treated interchangeably. Record reported and market prices separately. Fix EUR labels on USD calculations. Migrate database schemas without deleting history. Use event IDs and explicit snapshot times; daily uniqueness alone cannot identify distinct signals or permit intraday refresh.

Market provider interface: historical bars, historical quotes where entitled, live updates, calendars and actions. Validate each capability; missing data yields a visible quality status, not silent fabricated values.

Free initial provider: [Alpaca Basic](https://docs.alpaca.markets/us/docs/about-market-data-api), with cached historical SIP queries ending at least 15 minutes ago as described by the [market-data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq). Basic lists 200 historical requests/minute, history since 2016 and 30 live equity streaming symbols on IEX. Verify actual entitlement and coverage with sample requests before bulk collection. IEX is incomplete live market coverage; its volumes/quotes must not be relabelled SIP. Volume-driven paper validation will carry that mismatch prominently.

Use one-minute data for intraday candidates, daily bars for initial trend/reversion screening and open/close observations for overnight baselines. Keep source, feed, event time, availability time, ingest time, adjustment convention, data version, gaps and corrections. Fetch once and reuse across trials; no API call in a strategy's per-bar loop.

Execution accounting uses consistent tradable prices plus separate split/dividend events. Features may use a documented adjusted series without mixing conventions. Price-only and total-return benchmarks are explicit. Record dividends through overnight positions and split/merger/delisting outcomes. Do not claim survivorship-free S&P 500 research using today's constituents or a dataset lacking delisted stocks. Start with a declared watchlist and report that limitation; add historical membership once sourced.

## 6. Proposed clean file structure

This is the target layout; create packages as their milestone needs them. `__init__.py` files are implicit in Python package directories. Avoid empty scaffold files and broad `utils.py` dumping grounds.

```text
Trading bot demo/
├── pyproject.toml                       # package/dependency/tool configuration
├── README.md                            # setup, research workflow, examples
├── RESEARCH_PLATFORM_PLAN.md             # this design
├── .gitignore                           # secrets, caches, local state
├── configs/
│   ├── app.yaml                         # local paths, UI and provider choices
│   ├── data.yaml                        # feeds, timeframes and adjustment policy
│   ├── risk.yaml                        # shared controls
│   ├── universes/liquid_us.yaml
│   └── experiments/
│       ├── opening_ema_v1.yaml
│       ├── overnight_comparison.yaml
│       ├── insider_baselines.yaml
│       └── insider_overnight_v2.yaml
├── research/
│   ├── papers/                          # metadata and our method notes
│   ├── hypotheses/                      # timestamped specifications
│   └── screenshots/                     # user-provided references, optional
├── src/quantlab/
│   ├── cli.py                           # ingest/backtest/batch/report commands
│   ├── domain/
│   │   ├── market.py                    # Bar, Quote, CorporateAction
│   │   ├── insider.py                   # InsiderEvent, identity and availability
│   │   ├── trading.py                   # Signal, OrderIntent, Fill, Position
│   │   └── experiment.py                # spec, data identity and result manifest
│   ├── config/
│   │   ├── models.py                    # typed config validation
│   │   └── loader.py
│   ├── data/
│   │   ├── service.py                   # common retrieval/cache interface
│   │   ├── providers/
│   │   │   ├── base.py
│   │   │   ├── alpaca.py
│   │   │   ├── sec_edgar.py
│   │   │   ├── dataroma.py
│   │   │   └── openinsider.py            # optional discovery adapter
│   │   ├── legacy/insiderdata.py         # import without altering old project
│   │   ├── calendars.py
│   │   ├── security_master.py
│   │   ├── corporate_actions.py
│   │   ├── quality.py
│   │   └── availability.py              # point-in-time access rules
│   ├── storage/
│   │   ├── models.py                    # database schema
│   │   ├── repository.py                # persistence, not strategy logic
│   │   ├── market_cache.py              # immutable/versioned Parquet partitions
│   │   └── migrations/                  # numbered schema migrations
│   ├── features/
│   │   ├── moving_averages.py
│   │   ├── volatility.py                # ATR and realised volatility
│   │   ├── regression.py                # slopes, factor fits and residuals
│   │   ├── insider_activity.py          # cluster/freshness/routine features
│   │   └── session.py                   # opening range, gap, relative volume
│   ├── strategies/
│   │   ├── base.py                      # common Strategy contract
│   │   ├── registry.py                  # trusted modules and unique IDs
│   │   ├── baselines/
│   │   │   ├── buy_and_hold.py
│   │   │   ├── broker_daily_long.py      # inactive until instrument/time mapping exists
│   │   │   └── overnight_market.py
│   │   ├── intraday/
│   │   │   ├── opening_ema.py
│   │   │   ├── opening_range_breakout.py
│   │   │   ├── opening_range_15m.py
│   │   │   ├── opening_range_15m_v2.py
│   │   │   ├── opening_range_volume_v2.py
│   │   │   └── first_half_hour_momentum.py
│   │   ├── trend/
│   │   │   ├── regression_trend.py
│   │   │   └── regression_trend_v2.py
│   │   ├── mean_reversion/
│   │   │   ├── price_zscore.py
│   │   │   ├── residual_reversion.py
│   │   │   └── residual_reversion_v2.py
│   │   ├── overnight/
│   │   │   ├── close_to_open.py
│   │   │   ├── close_to_open_v2.py
│   │   │   └── weekend_only.py
│   │   ├── insider/
│   │   │   ├── post_filing_buy.py
│   │   │   ├── cluster_buy.py
│   │   │   └── opportunistic_buy.py
│   │   └── combined/
│   │       ├── insider_overnight_v2.py
│   │       ├── insider_trend_v2.py
│   │       ├── insider_pullback_v2.py
│   │       ├── insider_opening_ema_v2.py
│   │       └── regime_switch_v2.py
│   ├── exits/
│   │   ├── atr_trailing.py
│   │   ├── ema_momentum.py
│   │   ├── fixed_target.py
│   │   └── time_exit.py
│   ├── portfolio/
│   │   ├── sizing.py
│   │   ├── risk.py
│   │   ├── allocation.py
│   │   └── accounting.py
│   ├── backtest/
│   │   ├── engine.py                    # authoritative event replay
│   │   ├── execution.py                 # fills, gaps and stop sequencing
│   │   ├── costs.py                     # spread, fees, borrow and slippage
│   │   ├── screening.py                 # optional faster coarse screening
│   │   └── validation.py                # rolling windows and leakage checks
│   ├── experiments/
│   │   ├── runner.py
│   │   ├── batch.py                     # resumable bounded worker queue
│   │   ├── manifest.py                  # reproducibility hashes
│   │   └── selection.py                 # predeclared candidate gates
│   ├── analytics/
│   │   ├── metrics.py
│   │   ├── capm.py
│   │   ├── benchmarks.py
│   │   ├── uncertainty.py
│   │   ├── multiple_testing.py
│   │   └── reports.py
│   ├── execution/
│   │   ├── replay.py
│   │   └── alpaca_paper.py              # same intents, simulated broker only
│   ├── services/
│   │   ├── research.py                  # application use cases for CLI/UI
│   │   └── jobs.py
│   └── ui/
│       ├── app.py                       # local Streamlit entry point
│       └── pages/
│           ├── data_health.py
│           ├── strategy_library.py
│           ├── experiment_builder.py
│           ├── results.py
│           ├── insider_events.py
│           └── paper_monitor.py
├── tests/
│   ├── fixtures/                        # tiny known prices/filings/calendars
│   ├── test_strategy_contract.py
│   ├── test_point_in_time.py
│   ├── test_fill_sequence.py
│   ├── test_accounting.py
│   ├── test_calendar.py
│   ├── test_insider_import.py
│   └── test_reproducibility.py
├── notebooks/                           # exploratory work, no production dependencies
└── var/                                 # ignored runtime files
    ├── raw/                             # original filings/provider responses
    ├── market/                          # versioned Parquet market history
    ├── db/research.sqlite
    ├── runs/<run_id>/                   # configs, manifests, trades and equity
    ├── reports/
    └── logs/
```

## 7. Strategy contract and adding a manual strategy

Each module exports one Strategy class with a stable ID, version, typed parameter schema, description, provenance and declared data requirements. Contract: `on_event(context, event) -> list[Signal]`, plus initialization and optional session hooks. Context exposes only data available by the decision timestamp. Strategies cannot place broker orders, download data or write database rows. The portfolio layer converts signals to risk-approved OrderIntents; execution converts intents to fills.

Example metadata: `strategy_id='opening_ema'`, `version='1.0.0'`, `timeframe='5min'`, `requires=['bars_1min', 'exchange_calendar']`. A semantic code version identifies behaviour; `v2` filenames identify intentionally separate experimental ideas. Parameters such as EMA length, ATR multiplier and holding period are config values, not copies of the same source file.

To add your own strategy:

1. Copy a documented strategy template into the appropriate family directory.
2. Give it a unique ID, describe its timing and declare input requirements.
3. Implement signal logic using shared features and exits.
4. Add an experiment YAML with bounded parameter choices and benchmark.
5. Run the shared contract and small hand-calculated fixtures.
6. Restart/refresh discovery; it appears in the UI without editing UI or engine code.

Registry discovery imports only local trusted modules, requires explicit exported classes and rejects duplicate IDs and side effects. New dependencies are declared in `pyproject.toml`. Avoid hundreds of named variants; a new file is for a new rule or combination, while parameter trials share a module.

Combined V2 modules compose eligibility filters, entries and exits instead of copying V1 source. For example, insider_overnight_v2 uses the insider eligibility component and the overnight schedule component. Portfolio ensembles are different: allocate capital across independent strategy sleeves, model shared constraints and net conflicting intents. Never add equity curves that each assumed access to all the same capital.

## 8. Stack and storage

Proposed stack: Python, pandas/NumPy for tabular calculations, statsmodels for regression, PyArrow/Parquet for cached prices, SQLite plus schema migrations for research metadata, Pydantic for configs, pytest for material trading invariants, and Streamlit for the initial local UI. Dependency versions will be selected and pinned during implementation after checking Windows compatibility.

Use one authoritative event-driven engine for accepted results. A vectorized library such as VectorBT can be an optional coarse screener; accepted candidates must be replayed through the authoritative fill/accounting model and reconciled. Do not maintain two competing definitions of final performance.

Local UI, CLI and workers call shared application services. Heavy jobs run outside the UI process with job IDs, progress, cancellation and resumability. A bounded process pool reads cached data; one coordinator writes SQLite results, avoiding simultaneous worker writers. SQLite is sufficient for one user; a repository interface permits a later PostgreSQL migration if multi-user concurrency warrants it. No microservices are needed initially.

Database entities: securities and symbol history; data_versions and quality_findings; source_fetches; filings and insider_transactions; strategy_versions; hypotheses and experiment_specs; runs and trials; orders/fills/trades; metric_sets; validation_windows; paper_sessions and events. Link raw provenance through IDs. Large bar/equity/trade arrays may be files referenced by the database. Store monetary computations consistently and avoid rounding intermediate shares/P&L.

Every run stores source/config/code hashes, data version, random seed, universe definition, calendar, costs, currency, input cutoff and validation split. Append trials, including failures and rejected candidates; never overwrite V1 with an optimized V2 result.

## 9. Backtest accuracy requirements

- **No future information:** all signals, filings, indicators, regression fits and universe selection respect availability times. A future-data mutation test must not change earlier decisions.
- **Execution timing:** next-event fills for close-based signals; stops updated only when information exists. If both stop and target are touched within a bar and order is unknown, use finer data or a conservative explicit ambiguity rule. Never use the bar high to raise a stop before assuming the same bar low executes it.
- **Gap handling:** fill protective orders at attainable opening/next quote prices, not at an unavailable stop price. Overnight loss can exceed intended risk.
- **Costs:** fee model, bid/ask spread, slippage and impact assumptions, participation limits and borrow costs/restrictions for shorts. Report base and stressed costs. Without historical quotes, spread models are estimates and must be labelled.
- **Lifecycle:** splits, dividends, amendments, ticker changes and delistings; no automatic resurrection of bankrupt stocks or silent omission of unavailable exits.
- **Calendars:** daylight saving, holidays and early closes; no hard-coded Helsinki/New York offset.
- **Portfolio:** idle cash, buying power, pending orders, simultaneous signals, shared equity and exposure; no unlimited hypothetical capital across insider signals.
- **Feed identity:** do not mix free IEX volume signals and historical consolidated SIP measurements without an explicit comparison. Reject stale/incomplete inputs in paper mode.
- **Verification:** hand-calculated fixtures for long/short P&L, costs, gap stops, trailing sequence, insider publication, duplicate/amended filings, USD/EUR reporting and calendar transitions. Compare baselines with independent calculations.

## 10. Validation and experimental scale

Begin with a small declared universe and verified baselines. Register perhaps 10-20 families and 10-30 sensible configurations per family, yielding hundreds of trials without uncontrolled combinatorial explosion. Set CPU/RAM limits and trial caps per batch. Reuse data/features only when their cache keys include input/version/cutoff semantics.

Use chronological development and validation windows, rolling walk-forward evaluation and a final untouched holdout. Purge training outcomes that overlap validation boundaries and apply a declared embargo where appropriate. Never fit scalers, thresholds, insider classifications or beta windows on the full dataset. Leave enough historical warm-up before each evaluated interval.

Count every family, parameter, filter, exit and V2 trial. Evaluate parameter neighbourhood stability and performance by stock, sector, year and regime. Use time-block confidence intervals and account for multiple testing; add Deflated Sharpe Ratio and PBO diagnostics once the trial ledger and common return matrix are trustworthy. These diagnostics do not certify future success.

Metrics: net return/CAGR, mark-to-market drawdown, Sharpe/Sortino with stated sampling, alpha/beta with uncertainty, benchmark excess return, turnover, costs, exposure, trade count, average win/loss, worst gap, holding hours, time underwater, recovery censoring and capital requirements. An unrecovered position at dataset end is unresolved, not assumed recovered. Rank first by declared validity/coverage gates, then unseen robustness and risk; headline backtest return is not the primary sorting rule.

V2 must improve incremental unseen results over both parents. Include ablation tests: base, filter alone, entry alone and combination, all under the same portfolio model. Evaluate ensembles by net portfolio results and correlation through drawdowns, not only average strategy correlations.

After freezing a shortlist, run paper trading on new dates and compare signals/fills against simultaneous local replay. Record disagreements, collector latency, data feed differences, rejections and outages. Real-money use remains a separate future project with broker capabilities and terms checked at that time.

## 11. Local interface

Pages: Data Health; Strategy Library; Experiment Builder; Results/Compare; Insider Events; Paper Monitor. Strategy pages show required inputs, evidence status and parameters. Builder selects strategy, universe, dates, costs, risk and validation plan; a dry-run estimate shows data requirements, trial count and expected memory footprint before queuing.

Results show in-sample, validation and holdout separately, costs, benchmark, data quality, uncertainty, trades and parameter sensitivity. Selecting a trade reveals the observations and insider filings available at its decision time. Paper Monitor shows data freshness and position state. Export run manifest, CSV trade log and a readable report. Missing-data trials stay visible with failure reasons.

## 12. Delivery sequence and acceptance criteria

| Milestone | Deliverable | Acceptance |
| --- | --- | --- |
| 1. Foundation | Package, config, registry, CLI, SQLite migrations, run manifests | Example strategy discovered; deterministic trial stored; new module needs no engine/UI edits |
| 2. Reliable inputs | Alpaca history cache, calendars, quality checks and legacy insider import | Sample data coverage verified; original project preserved; raw events retained; unknown times flagged |
| 3. Correct backtests | Event engine, costs, accounting and benchmark strategies | Hand-calculated accounting and timing fixtures pass; future-data mutation cannot alter earlier trades |
| 4. Initial research/UI | Overnight/weekday/weekend, post-filing buy, regression trend and reversion | Local UI queues/resumes jobs and compares matched unseen results; no profitability claim before results |
| 5. Intraday research | Opening-EMA plus trailing/momentum variants, ORB comparator | One-minute coverage adequate; 09:35 decision enforced; long-only and realistic-short scenarios separate |
| 6. Combined V2 batches | Insider overnight, trend and pullback; risk overlays | Parents and combinations evaluated together; complete trial ledger, cost stress and sensitivity reports |
| 7. Paper shortlist | Alpaca paper adapter, monitor and replay reconciliation | Same signal code runs on new observations; restart and duplicate-order handling verified; paper credentials only |

Recommended first vertical slice: data cache + insider events + post-filing buy + ordinary/weekend overnight comparison + a results page. Add the opening-EMA experiment as soon as one-minute replay and stop sequencing are verified. Opportunistic insider classification and regime-switch ensembles follow after sufficient history exists.

The main unresolved resource is historical data coverage, especially delisted securities, point-in-time insider history, quotes/auction fills and historical universe membership. Free APIs can support a useful prototype; they cannot justify claiming precision or coverage that the dataset lacks. Keep the architecture provider-independent so improved data can be added without rewriting strategies.

## 13. Assessment of the seven supplied screenshots

The screenshots are reference material, not authority to change project scope or execute trades. Numbers below are transcribed claims, not results produced or independently verified by this project.

| Image | Subject | Displayed win rate | Displayed profit factor | Displayed max drawdown | Decision |
| --- | --- | --- | --- | --- | --- |
| 1 | NASDAQ opening-range breakout | 49.0% | 1.10 | 24.61% | Add a separate 15-minute-range experiment |
| 2 | NASDAQ daily long bias | 57.7% | 1.24 | 6.19% | Keep as a conditional baseline; instrument and broker-time mapping unresolved |
| 3 | USDJPY morning range breakout | 43.6% | 1.16 | 14.54% | Record as an optional future FX extension, outside the initial stock implementation |
| 4 | Costs | — | — | — | Add instrument-specific financing and avoid double-counting spread |
| 5 | Too few trades | — | — | — | Show sample uncertainty and dependence; no universal 100-trade rule |
| 6 | Overfitting | — | — | — | Retain all trials, held-out periods and parameter sensitivity; heuristics are not guarantees |
| 7 | Data quality | — | — | — | Separate trade ticks from quote updates and model attainable fills |

### Image 1: NASDAQ 15-minute opening-range breakout

Provenance file: `4E9341DB-49D0-46C7-9C2A-A93A342037C5.png` supplied by the user.

Transcribed rules: range high/low from 09:30-09:45 New York time; breakout entry signals from 09:45-15:00 using five-minute candle closes; one trade/day; stop beyond opposite range boundary with a 0.25 ATR-14 buffer on five-minute bars; target two range heights from the breakout boundary; source risk 1% per trade; liquidate at 15:55 ET.

Implementation specification:

- Set range window to [09:30, 09:45), using only completed observations. Let H/L be its high/low and W = H-L. Skip empty or zero-width ranges.
- A long signal requires a later completed five-minute close > H; a short signal requires a close < L. Proposed window interpretation: signal close time >09:45 and <=15:00, then next-event execution. This means first possible signal is 09:50; a different cutoff interpretation is a separate version.
- At fill, long stop = L - 0.25 ATR and long target = H + 2W; short stop = H + 0.25 ATR and short target = L - 2W. ATR is based on available completed bars and frozen for these initial levels. Preserve the distinction between breakout-boundary target and actual entry price.
- Two range heights is **not necessarily a 2:1 reward/risk trade**: risk includes the range, ATR buffer, entry overshoot, costs and gaps. Skip entries whose price has already passed the target or whose initial levels are invalid; record the reason.
- Proposed one-trade interpretation: one filled entry per instrument/session. If the source meant one trade for the entire portfolio, record that as a different allocation rule.
- Source-faithful risk scenario uses 1%; the platform's lower common-risk scenario is an explicitly labelled adaptation. Risk cannot be enforced by stops through gaps.
- Ordinary time exit is 15:55; for early closes use session close minus five minutes. No new entry after that cutoff.
- The exact NASDAQ instrument is unknown. NASDAQ-100 cash index, QQQ ETF, NQ/MNQ futures and a broker CFD have different trading sessions and accounting. Use QQQ for a stock/ETF adaptation once appropriate data exist, without claiming to reproduce the displayed results.

V2 comparisons: base; base plus fresh public insider-stock eligibility on individual equities; base plus relative opening-volume filter. The insider version changes the universe from NASDAQ to stocks and is a new hypothesis. Test each addition separately before a combined filter. Continue to test the user's first-five-minute EMA rule independently: it is not this screenshot strategy.

Profit factor is gross winning P/L divided by gross losing P/L under a stated cost convention. A reported 1.10 means a relatively narrow observed surplus in that source calculation; it cannot be assessed without the period, trade count and costs. Obtain those before interpreting its curve or comparing its drawdown with other screenshots.

### Image 2: daily long bias

Provenance file: `D09D2FC9-585B-4540-A201-D729C5604F61.png`.

Transcribed rules: buy each trading day at 01:05 broker time, exit at 23:50 the same broker day; no stop, profit target or filters; source rationale is upward index drift and avoiding most overnight swap.

This cannot be translated into a US regular-session stock strategy without the broker timezone, daylight-saving schedule, contract and trading hours. References to swap and broker time suggest a leveraged FX/CFD-style source setup, but the exact product is not established. Keep `broker_daily_long.py` unavailable until these prerequisites are resolved. Do not assume 01:05 broker time is a US overnight entry or that 23:50 avoids financing for every broker.

A QQQ regular-session open-to-close long is a useful exposure benchmark but must be labelled an adaptation with different holding hours. Compare it with QQQ buy-and-hold and overnight returns using the same capital/cost model. The source sizing/leverage are unspecified, so 6.19% displayed drawdown does not establish lower inherent risk. V2 may add a market-trend filter; no-stop V1 stays an explicit simulation baseline with a notional cap, not a live default.

### Image 3: USDJPY morning range

Provenance file: `5F6F2CDE-6BE1-430A-A4B0-729FF38ABBE5.png`.

Transcribed rules: range from 03:00-06:00 broker time, stated as GMT+2/GMT+3 in summer; at 06:00 place a buy stop at range high and sell stop at range low; opposite-boundary protective stop, no target; one trade/day and cancel the other entry after a fill; at 18:00 close the trade and cancel outstanding entries; fixed monetary risk per trade, amount unspecified.

Record a future `strategies/fx/morning_range_breakout.py`, enabled only when an FX provider and contract-aware accounting exist. Requirements: broker DST rules, executable bid/ask history, pip/lot specification, currency conversion, margin/financing and order cancellation semantics. Simulating two opposing entry orders requires handling cancellation latency and double-fill races; do not assume cancellation is instantaneous. If bar data touch both entry boundaries, sequencing is unknown. A full stock-market beta/CAPM evaluation is not a sufficient benchmark for this FX experiment.

Porting its range concept to stocks would be a separate strategy with exchange-session windows; do not silently use FX hours or source performance numbers. No FX implementation is added to the initial milestones merely because the screenshot contains one.

### Images 4-7: testing guidance and corrections

**Costs (image 4):** spread, fees, slippage and financing all matter, but their applicability and units depend on instrument/account. Cash-funded long stocks do not inherit a CFD's nightly swap rule; margin interest, short borrow and FX conversion are distinct. CFD swap may be charged or credited under its rules, not always paid. Broker commission is not universally per lot. If fills already use buy ask/sell bid, do not subtract another full spread on top. Add `instrument_type`, `fee_model`, `financing_model`, `borrow_model` and `rollover_calendar` to execution configuration. Primary reference: [Trading 212 overnight interest explanation](https://helpcentre.trading212.com/hc/en-us/articles/360007113997-What-is-overnight-interest).

**Sample size (image 5):** the warning about small samples and one dominant year is useful. However, 100 trades or 100 trades/year is not a validity threshold. Many simultaneous stocks responding to one market event are not independent evidence. Show number of trades, independent sessions/events, years, uncertainty, outlier contribution and regime coverage. Use dependent-data resampling. Keep small samples visible as uncertain rather than requiring a long-horizon strategy to trade more frequently just to pass a quota.

**Overfitting (image 6):** count every version, including manual tweaks, rejected runs and deleted code. A final 20-30% holdout is a possible starting split, not a universal choice; dates must supply sufficient observations and represent relevant regimes. Reusing a viewed holdout for V2 development makes it a development sample. Approximately +/-20% parameter sensitivity can reveal fragility, but a collapse does not prove luck and stable neighbours do not prove skill. Integer parameters need meaningful nearby values. Store actual sensitivity choices and all resulting trials. Continue chronological walk-forward, overlap purging and multiple-testing evaluation described above.

**Data quality (image 7):** OHLC bars cannot establish the order of all within-bar events. Trade ticks provide executed transactions; quote ticks provide bid/ask updates. Trade ticks alone do not reconstruct the spread, every venue's state or the price an order could obtain. Even quotes/ticks cannot guarantee stop fills: latency, gaps, available size, order priority and broker trigger rules matter. Maintain separate bar/trade/quote replay capabilities and explicit feed identity. Use one-minute bars for screening, finer historical trade/quote replay for sensitive shortlist cases where available, and new-date paper reconciliation. A missing quote dataset remains a modelling limitation, not a reason to fabricate fills. Primary references: [Alpaca market-data types](https://alpaca.markets/sdks/python/market_data.html) and [SEC order basics](https://www.sec.gov/files/trading101basics.pdf).

### Changes to delivery priorities

Add 15-minute ORB after intraday execution verification in milestone 5. Include its source-claimed metrics only in the reference metadata, never in our results leaderboard. Keep broker daily long conditional and FX morning range as an optional later extension. The initial priority remains credible stock/ETF data, insider-event availability, portfolio accounting and overnight comparisons. Risk, financing and replay improvements belong in shared code so they apply equally to all strategies.
