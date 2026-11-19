# QuantLab — local stock strategy research

A Python research application with a black chart dashboard and top tabs. It runs locally, stores datasets and experiment history, and compares strategy variants with explicit execution assumptions. It starts with labelled **synthetic data**, so it can be used without keys or money.

**There are no broker order methods or live execution routes.** The Replay tab is a session-by-session local rehearsal. Optional broker connections only read Alpaca paper or Trading 212 demo accounts. This release does not execute live or broker paper orders.

## Start on this computer

Double-click **Start QuantLab.cmd**, or run:

```powershell
.\.venv\Scripts\python.exe run.py serve --open
```

Open <http://127.0.0.1:8765>. The server binds only to the loopback interface. Keep the terminal running; Ctrl+C stops it. To use another port: `run.py serve --port 8766 --open`.

The project virtual environment is already prepared on this computer. Windows timezone data was copied from the locally installed runtime because sandboxed package downloads were unavailable. No API keys are required for sample data.

## Install on another computer

Python 3.11 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py serve --open
```

Only Windows IANA timezone data is an external runtime dependency. Optional development formatters are pinned in requirements-dev.txt. The server, strategies, analytics, database and frontend have no CDN or framework dependencies. `pip install -e .` is optional if you want the `quantlab` command.

## Use the application

1. **Charts:** choose a dataset, change the date range, hover for prices, select a ticker to enlarge. SPY is a tradable S&P 500 ETF reference, not the actual S&P 500 index series. Sample prices are fabricated.
2. **Strategies:** select a module, symbols, dates and parameters. Set capital, allocation and execution costs. Each new run defaults to **$1,000 of independent virtual capital**; stocks within a run share that portfolio, while sweep variants each receive a fresh balance. Existing experiments and replay sessions retain their original capital. The browser migrates its old default once; subsequent changes remain editable in Settings. Run one test, a JSON parameter sweep (up to **500 variants per batch**, unlimited batches), or three fixed-parameter walk-forward evaluation windows.
3. **Experiments:** queued and completed runs, failures, parameters, dates, metrics and batch identifiers persist in SQLite. Sort by return, Sharpe or drawdown. All variants remain recorded, including unsuccessful ones. The interface shows the most recent 500; the database retains all.
4. **Results:** equity and SPY price reference curves, drawdown, yearly results, trade history, holding time, recovery observations, costs, CAPM beta/realized alpha and assumptions. Export the full JSON manifest or trades CSV.
5. **Insiders:** inspect imported SEC transactions, CSV events or legacy snapshots. Synthetic events use a separate source filter and cannot leak into real-data tests.
6. **Data:** download Alpaca historical bars or import your own CSV. Dataset hashes include bars and provenance. Real prices are never replaced with fabricated fallbacks if a provider fails.
7. **Replay:** start from the selected strategy. Advance one stored session at a time; open positions remain open. It recomputes the revealed prefix deterministically. Read-only account/quote connectors are available below it.
8. **Settings:** browser-local execution defaults and connection status. The browser receives configured/not-configured flags, not secrets.

## Strategy library

| Module | Idea | Data |
|---|---|---|
| `buy_hold.py` | Initial next-open allocation with idle cash and portfolio limits | Daily / intraday |
| `opening_ema.py` | First 09:30–09:35 New York candle vs EMA12; ATR trail / EMA loss exit | 5 minute |
| `opening_range.py` | Screenshot's 15-minute opening range, ATR stop, 2-range target, one trade per session | 5 minute |
| `regression_trend.py` | Positive rolling log-price slope and minimum R²; trail / slope-loss exit | Daily |
| `mean_reversion.py` | Price z-score entry, recovery/time/ATR exit | Daily |
| `residual_reversion.py` | Rolling stock-market return regression and cumulative residual deviation | Daily + SPY |
| `overnight.py` | Scheduled close-to-next-open exposure; weekday filter | Daily |
| `overnight_weekend.py` | Friday close to next trading-session open | Daily |
| `insider_purchase.py` | Public code P purchase, threshold and holding window | Daily + public filings |
| `insider_cluster.py` | Multiple distinct buyers in a public-event window | Daily + public filings |
| `insider_overnight.py` | Public purchase + overnight schedule | Daily + public filings |
| `insider_trend.py` | Public purchase + regression trend | Daily + public filings |
| `insider_pullback.py` | Public purchase + mean-reversion entry | Daily + public filings |
| `insider_opening.py` | Public purchase gates opening EMA, long only | 5 minute + public filings |
| `profit_target.py` | Net USD cash target after modelled order costs, loss/time limits | Daily |

These are explicit research prototypes, not verified reproductions of paper results. The regression residual module is not a claim to replicate a statistical-arbitrage paper. A sweep creates parameter variations, not hundreds of independent strategy families.

The opening EMA uses completed regular-session bars, with previous sessions providing warmup. Its EMA is initialized over a finite trailing window (at least 200 bars or five EMA lengths). Intraday strategies close by five minutes before the exchange session end; early close sessions use the imported calendar. The screenshot's 1% risk is a configurable sizing input, not a guaranteed loss limit. Shorting is opt-in for the two opening strategies; shorts reserve full collateral and assume availability at a fixed borrow rate.

The screenshot's broker-time NASDAQ daily-long strategy and USDJPY range strategy remain in the research plan: the instrument, broker clock, financing and FX data requirements are not specified well enough to present them as runnable stock strategies. Routine/opportunistic insider classification requires a longer filing history. No historical claimed win rate or profit factor from those screenshots is treated as evidence.

## Real market data and insider filings

Copy `.env.example` to `.env` and enter keys locally. Do not put secrets into the frontend or version control.

```dotenv
ALPACA_API_KEY=your_paper_key
ALPACA_API_SECRET=your_paper_secret
SEC_USER_AGENT=Your Name your-real-contact-email
TRADING212_DEMO_API_KEY=your_demo_key
TRADING212_DEMO_API_SECRET=your_demo_secret
```

Use paper/demo credentials. The Trading 212 connector's hostname is fixed to `demo.trading212.com`; its only operations are account summary and positions. The Alpaca account read is fixed to `paper-api.alpaca.markets`. Alpaca market data uses its separate data API. Account responses are shown locally in the Replay tab.

Historical Alpaca downloads paginate bars, request split adjustment, and use the exchange calendar for holidays and early closes. SIP requests end at least 16 minutes ago to accommodate delayed historical access; actual access depends on account entitlement. Latest IEX quote reads are incomplete exchange coverage, not consolidated real-time market data. The current UI refreshes research/job state automatically; it does **not** run an unattended trading or live quote strategy loop. Provider calls have timeouts and bounded retries; jobs run serially to limit API pressure.

SEC fetches read recent submission metadata and non-derivative Form 4 transactions. The acceptance timestamp is the earliest assumed public availability; it does not model your scraper's delivery delay. Amendments are excluded rather than silently revising earlier decisions. The adapter does not yet archive every raw SEC response or fetch the complete historical submissions archive. For archival research, retain raw filings and model actual delivery timestamps.

The existing `InsiderData` project is untouched. Its database import opens SQLite in **read-only mode**. Snapshot rows lack a trustworthy historical public timestamp and transaction code, so they display with unknown availability and cannot generate historical signals. This avoids using insider purchase prices as executable entry prices.

## CSV formats

Bars:

```csv
symbol,time,open,high,low,close,volume,session,session_end
SPY,2026-10-05T13:30:00+00:00,570,575,568,574,12345678,2026-10-05,2026-10-05T20:00:00+00:00
```

`time` is the bar's start. A daily bar starts at session open and becomes available at `session_end`. Intraday bars become available after their stated interval. Only import regular-session bars with correct exchange session dates and close times. Offset-free timestamps, duplicate symbol/start pairs, nonfinite values and invalid OHLC are rejected. CSV currency is USD in this release. The importer cannot independently prove your calendar, source, corporate-action treatment or universe completeness.

Insiders:

```csv
symbol,insider,transaction_date,shares,price,code,available_at,source
AAPL,Example Buyer,2026-10-01,1000,200,P,2026-10-02T18:05:00+00:00,verified filing export
```

Optional: `event_id,observed_at,role,accession,ownership,amended`. A missing transaction code is **UNKNOWN**, never assumed to be a purchase. Missing `available_at` excludes the row from signals. Purchase price is only a purchase-value feature, never the strategy's execution price. Verify your supplied public timestamp: the importer trusts explicit CSV provenance.

## Backtest model and its limits

- Completed-bar signals fill at the next available bar open. Close-to-open eligibility is calendar/public-event based; insider eligibility stops five minutes before the scheduled close. Official-close fills are idealized auction proxies, clearly labelled in results.
- Protective stops and targets use OHLC. A gap through a stop fills at the worse open; a candle that touches both exits uses the stop first. Trailing stops updated from a completed candle apply to the following candle. No order-book queues, partial fills, auction eligibility, latency, actual quotes or volume-based fills are simulated.
- One shared cash portfolio covers all selected symbols. Fractional shares are approximated to six decimals. Allocation, maximum positions and stop-distance risk limit entry size. Simultaneous entries are processed in deterministic ticker order, not optimized for hindsight.
- Costs include full spread divided between entry/exit, slippage each side, fixed commission and time-based short borrow. Unpaid borrow is accrued in equity. Cash stock holding does not incur a generic CFD swap. There is no FX model, tax model or cash-interest return.
- The drawdown circuit breaker blocks new entries and does not force liquidation. Gaps and already open positions can exceed configured thresholds. Terminal positions are explicitly liquidated at their last test close, including losers; replay retains them.
- Daily equity includes idle cash and unrealized marks. Sharpe uses daily equity returns and a configurable annual risk-free input divided by 252. CAPM uses aligned SPY price returns and reports an historical regression intercept annualized arithmetically; it is not an expected-return forecast. Dividends and historical delisted membership are not automatically covered.
- MAE/MFE are full-bar bounds: bar extremes may happen after the assumed exit. Recovery time is measured from completed-bar observations while held and is not precise intrabar recovery. Recovery fraction refers to closed trades that were observed negative and ended net positive.

No free bar dataset makes these execution estimates exact. Validate promising results with consolidated quotes/trades, corporate actions, a historical universe, realistic auction/borrow assumptions, and independent future paper observations before considering live work.

## Holdouts and experiment hygiene

The last 20% of dataset sessions is reserved by the research service. Ordinary tests cannot execute into it. The explicit final-evaluation action consumes one frozen candidate per dataset, even if it fails. A new identical dataset import has the same identity when its provenance is unchanged. Changing provenance or using external code can bypass this tool boundary, so it is not a substitute for research discipline. Price charts remain available: the execution reservation does not prevent inspecting prices.

Walk-forward runs use fixed parameters in three nonoverlapping evaluation windows over the latter 60% of the exploratory period. Prior bars provide indicator warmup, but prior positions do not carry into a fold. It does not silently select or refit winners. For nested parameter selection, run train/validation batches explicitly and keep the final holdout untouched.

Counting variants and reserving a holdout helps, but formal multiple-testing correction, deflated Sharpe, purged cross-validation and confidence intervals are future work. A 100-trade threshold or a 20% parameter nudge cannot prove an edge. Compare nearby parameter settings, years, costs and market conditions, including failed ideas.

## File structure

```text
Trading bot demo/
├── run.py                       # Source checkout launcher
├── Start QuantLab.cmd           # Windows launcher
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── .env.example                 # Credentials template; .env is ignored
├── README.md
├── RESEARCH_PLATFORM_PLAN.md     # Research background and future architecture
├── docs/
│   ├── DESIGN.md
│   └── VALIDATION.md
├── scripts/
│   └── verify_sample.py          # Every module + 200-variant integration sweep
├── src/quantlab/
│   ├── cli.py
│   ├── server.py                # Loopback HTTP API and static frontend
│   ├── domain.py                # Bars, public events, signals, positions
│   ├── features.py              # EMA, ATR, regression, z-scores
│   ├── engine.py                # Deterministic portfolio event replay
│   ├── analytics.py             # Daily metrics, CAPM, yearly breakdown
│   ├── jobs.py                  # Queue, sweeps, holdout, session replay
│   ├── storage.py               # SQLite + compressed bars + run exports
│   ├── data/
│   │   ├── providers.py         # Alpaca, SEC, CSV, read-only legacy adapter
│   │   ├── trading212_demo.py   # GET-only demo account connector
│   │   └── synthetic.py         # Deterministic fabricated fixtures
│   ├── strategies/             # One file per strategy + registry/common helpers
│   └── ui/
│       ├── index.html
│       ├── style.css
│       └── app.js               # Local canvas charts, tabs, forms, jobs
├── tests/
│   ├── test_research.py
│   └── test_http.py
└── var/                         # Generated, ignored by Git
    ├── research.sqlite          # Datasets, events, runs, holdout, replay
    ├── market/*.csv.gz
    ├── runs/<run-id>/
    │   ├── result.json
    │   ├── manifest.json
    │   └── trades.csv
    └── verification.json
```

The application is one process with distinct domain, execution, storage, adapters and UI layers. It avoids empty service scaffolding. Strategy histories are bounded, read-only prefix views; indicator slices copy only the requested lookback, rather than copying a complete price history for every bar. Future migrations can split providers, add quote replay, or replace the chart renderer without placing API calls into strategy modules.

### Add a strategy

Create `src/quantlab/strategies/my_strategy.py`:

```python
from ..domain import Signal

def evaluate(ctx):
    # ctx.history contains only bars completed through this decision.
    # ctx.public_events() respects the public timestamp cutoff.
    if ctx.position:
        return None  # Or Signal(0, "exit reason") for next-open exit.
    if len(ctx.history) >= 20 and ctx.bar.close > ctx.history[-20].close:
        return Signal(1, "20-bar momentum", stop_distance=ctx.bar.close * .03,
                      exit_mode="trail", max_bars=30)
    return None
```

Add one definition in `strategies/__init__.py` with ID, name, family, timeframe, default parameters and description. New numeric parameters are exposed automatically in the UI and grid; add parameter-specific bounds in `engine.validate` where needed. Do not read providers, future bars or broker APIs from the strategy. Restart the server after Python changes. Defaults do not allow shorting unless explicitly supported by the engine's capability checks.

The V2 modules demonstrate composition: reuse a trend/reversion signal and gate its entry with an insider condition, while continuing to evaluate exit rules even if the insider window has expired.

## Tests and verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/verify_sample.py
```

Tests use isolated temporary databases and synthetic fixtures. HTTP tests need permission to bind/connect to a temporary local socket. The integration script writes labelled verification experiments to the main research database and checks all 15 modules plus 200 sweep variants. Stop running other queued experiments before launching that script: startup recovery marks unfinished runs interrupted.

Preserve `var/` to retain your dataset and experiment history. Stop the server before taking a simple file-copy backup of it; SQLite uses WAL sidecars while running. API keys are kept separately in `.env`.

## Implementation boundaries

This is a runnable research release. Consolidated streaming quote replay, archival SEC ingestion, formal multiple-testing statistics, historical universe/corporate-action reconciliation, broker paper order execution, live manual-approval execution and deployment are not included. They require data/account access and separate validation. The larger research plan remains a roadmap, not a claim that those later components are implemented.
