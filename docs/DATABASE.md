# Local research storage

The application uses SQLite at `var/research.sqlite`. There is no separate database server or cloud storage.

| Storage | Contents |
| --- | --- |
| `datasets` | Identifiers, hashes, source, adjustments, coverage and bar-file metadata |
| `insider_events` | Deduplicated public events and identified synthetic/legacy records |
| `runs` | Every experiment's settings, status, summary metrics and batch identifier |
| `holdouts` | Reserved dates and whether the final evaluation has been consumed |
| `replay` | Current local replay configuration and state |
| `data_audits` | Latest structural audit per checked dataset; created on first audit |
| `var/market/*.csv.gz` | Compressed OHLCV price bars |
| `var/runs/<id>/` | Result JSON, trade CSV when trades exist, reproducibility manifest |

Database reports actual record and file counts. Experiments displays the latest 500 records without deleting older records. Credentials are in `.env`. Saved risk defaults use browser local storage. A draft combined portfolio lives in the current page until submitted; submission saves its full configuration in `runs`.

Back up the **entire `var` directory after stopping the app**. SQLite alone omits prices and detailed results. SQLite uses WAL mode; stopping the app avoids inconsistent copies during writes. Protect `.env` separately and never include keys in shared exports. Automatic backups are not implemented.

## Virtual capital

Individual runs default to $1,000. Symbols within a run share its balance. Sweep variants are independent experiments. Replay persists until replaced. Existing results keep their original balances.

Combined portfolios divide one total balance between 2–5 allocations whose weights sum to 100%. Each receives at least $100, uses its own risk limits and cash, and shares the same dataset/date range. There is no lending between allocations, rebalancing, order netting or portfolio-wide liquidation rule. Results sum equity and report the largest aggregate single-stock gross exposure observed at session closes. This is fixed allocation testing, not a shared-cash order scheduler.

Whole-share mode rounds entries down and rejects unaffordable orders. Fractional mode permits six decimal places, a simulation assumption rather than a broker capability guarantee.

## Data and calendar checks

Data → Historical data audit checks bars against NYSE sessions: missing/unexpected dates, timestamp boundaries and incomplete coverage. Missing bars can reflect listing dates, halts or no trades. Audits do not fill missing prices or independently verify prices, adjustments, spreads or historical universe membership. Synthetic examples remain synthetic after an audit.

Real history can be imported through Alpaca or CSV from a verified source. Alpaca credentials must be set locally before authenticated downloads. No credentials were available during implementation; a real historical download remains unverified.

The dashboard uses exchange-calendars for Helsinki, NYSE, London and Xetra, including holidays and special closes. Scheduled open describes the calendar, not live exchange status. Ad hoc closures and halts may not be reflected. Hours convert to Helsinki time for the displayed date.
