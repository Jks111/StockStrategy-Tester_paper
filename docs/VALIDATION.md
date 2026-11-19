# Verification record

This file describes software validation, not financial evidence. All exercised strategy results use fabricated starter data.

## Automated checks

Final suite: **39 tests passed**. Latest integration sweep: **15 modules + 200 variants = 215 completed runs, zero failures**. The machine-readable record is in `var/verification.json`. Ruff lint and JavaScript syntax checks pass.

- Unit and local HTTP tests cover next-open execution, gap-through-stop pricing, adverse stop/target ordering, delayed trailing stops, flat-price costs, net cash targets, short collateral/equity accounting, portfolio position limits, drawdown entry halts, terminal liquidation and retained replay positions.
- Insider checks cover filing-time rather than transaction-price entry, missing availability, future events, amendments, preclose cutoff, unknown CSV codes, XML transaction codes and read-only legacy database import.
- Data/experiment checks cover dataset provenance hashes, finite prices, duplicates, exchange-calendar/pagination normalization, heldout dates, single final evaluation, grid prevalidation, maximum batch size, separate walk-forward windows and deterministic revealed prefixes.
- CAPM regression is checked against an artificial series with known beta 2 and zero intercept.
- Local HTTP checks cover missing mutation tokens, cross-origin rejection, Host checks against DNS rebinding, blocked static traversal, no live order route and CSV exports.
- `scripts/verify_sample.py` exercises every registered module and 200 regression parameter variants. It records all runs in SQLite and saves `var/verification.json`.

## Browser verification

The local dashboard is opened in the Codex in-app browser. Chart rendering, strategy configuration, submitted jobs, completed result selection, equity/drawdown charts, replay and tab navigation are checked against rendered UI state. The implementation uses no remote chart dependencies. Responsive CSS is included; phone viewport behavior has not been separately verified. Eight snapshots were successfully imported from the original InsiderData database in read-only mode, and their snapshot dates are separate from unknown transaction/publication dates.

## Not verified with live services

No Alpaca, SEC contact or Trading 212 credentials were supplied. No authenticated provider download or account call was executed. Provider request shape, calendar handling and pagination use mocked fixtures; endpoint documentation was checked, but successful account entitlement and real response coverage still need a real connection test.

No real-money orders, paper-broker orders or external account changes were made. Synthetic profitability is not evidence of an edge. Accurate intraday and overnight financial validation still requires appropriate quotes, execution/auction assumptions, corporate actions, historical membership, delivery timestamps and unseen future observations.

## Provider references

- [Alpaca historical stock bars](https://docs.alpaca.markets/us/reference/stockbars)
- [SEC EDGAR API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [Trading 212 authentication and environments](https://docs.trading212.com/api/section/authentication)
- [Trading 212 demo account summary](https://docs.trading212.com/api/accounts/getaccountsummary)

These references informed the read-only adapters. Broker/API terms need a fresh review before adding any execution component.
