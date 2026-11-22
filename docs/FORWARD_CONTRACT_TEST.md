# QuantLab forward contract test

The study watches LMT, NOC, and RTX against ITA, the iShares U.S. Aerospace & Defense ETF. It records hypothetical outcomes without connecting to an order endpoint.

## Run a batch

Run these commands from the checkout using the existing QuantLab virtual environment.

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py scripts/collect_contract_announcements.py --source sam
& $py scripts/collect_company_evidence.py
& $py scripts/collect_usaspending.py --start YYYY-MM-DD --end YYYY-MM-DD
& $py scripts/forward_contract_test.py prepare
```

USAspending dates are action dates. Recheck overlapping ranges for delayed obligations. The study can start from saved USAspending data while a new download is pending. Evidence more than 14 days old cannot by itself enroll an award. The amount filter is $1 million in absolute reported award amount or transaction obligation. After freezing an award, retain the frozen inputs even when the source files change.

Send `var/forward_contract_test/contract_only.txt` to **QuantLab · Event Scoring** first. Ask it to score only that file. After the baseline file is saved, send `with_context.txt` and ask for a fresh score using both the contract and supplied company evidence, without showing the baseline results. Both arms write one JSON object per line with `event_id`, integer `score` (1–10), `direction`, `confidence`, and `rationale` (240 characters maximum). The contextual arm also supplies `evidence_ids` from its packet. If that packet is empty, use an empty context score file. Reusing the same chat makes this a sequential comparison, since the scorer can remember earlier turns.

```powershell
& $py scripts/forward_contract_test.py register --contract-scores var/forward_contract_test/contract_scores.txt --context-scores var/forward_contract_test/context_scores.txt
& $py scripts/forward_contract_test.py report
```

Register within two days of freezing a batch. Registration uses the current UTC timestamp and fixes the first regular-session open strictly after registration for both arms. Every candidate receives forward measurements even when neither arm selects a paper buy. Company evidence has a 45-day lookback. An exact award ID mention is flagged separately from issuer context, which can still support or challenge the company outlook. Treat an ID mention as a link to inspect for confirmation.

For batches with both ordinary company evidence and Form 4 context, score `without_insider.txt` before `with_context.txt`. Save the additional scores in the contextual format and pass `--without-insider-scores PATH_TO_SCORES` to `register`. The two packets share the same contract and ordinary company evidence, and the latter adds up to two Form 4 summaries. When Form 4 is the only context, the contract-only score also serves as the comparison without insider data. The report includes the paired score, decision, and return comparison under `incremental_insider`.

## Read the TXT outputs

- `enrollments.txt` holds frozen scores, the entry session, and the exit sessions.
- `returns.txt` holds split-adjusted stock and ITA price returns from entry open to the close of trading sessions 1, 5, and 20. Excess return is the stock return minus the ITA return in percentage points. These are gross price returns before dividends, spread, and trading costs.
- `comparison.txt` reports completion counts, score-bucket returns, the buy count and mean buy return above ITA, and decision changes on the same events. A paper buy needs a score of 8 or higher, positive direction, and high confidence. Multiple events on the same ticker and entry session share a price outcome.

Run `report` after future closes to fill the pending horizons. It uses the existing read-only Alpaca SIP bar connection and waits for the delayed daily bar. Collection, registration, and measurement call no AI model. The scoring chat is the model step.

## Public follow-ups and insider filings

```powershell
& $py scripts/collect_public_evidence.py
& $py scripts/collect_insider_transactions.py
& $py scripts/forward_evidence_test.py followup prepare
& $py scripts/forward_evidence_test.py insider prepare
```

The public collector reads Federal Register notices, the GAO and Finnish Government RSS feeds, official foreign-sales notices, subawards for known watchlist prime awards, and GLEIF identity records. GLEIF rows inform identity checks. Regulatory proposals, proposed foreign sales, and subawards each keep their own stage and amount labels. Company evidence added after the original enrollment can form a new forward observation if its release or action date is also later. Original contract enrollments and entry windows stay intact.

The Form 4 collector reads recent SEC issuer submissions and the original ownership XML. Codes P and S are nonderivative purchases and sales, which can be open-market or private transactions. Grants, withholding, and option exercises are excluded. Transaction dates are separate from EDGAR acceptance and local first observation. The trading-plan flag applies to the filing. Unpriced rows keep their share count without inventing a value. Filing summaries group transaction lines for scoring. Form 4 amendments remain marked and are ineligible as fresh standalone entries. Blocked raw filings remain retryable. Use `--sec-contact` to declare an operator contact or `--xml-dir` to import original SEC XML files named by accession.

The insider cohort accepts filings within 14 days of SEC acceptance and groups by filing accession. Score `insider_only.txt` first in the scoring chat, then `with_context.txt` separately. The follow-up cohort has only a new contextual scoring step and retains the original contract score as its reference. For contextual scores, include `evidence_ids`. For follow-up scores also add `evidence_relations`, keyed by the same IDs, with `corroborates`, `challenges`, or `context_only`. Award corroboration requires a matching award ID in the supplied evidence. Use an empty context file when the context packet is empty.

```powershell
& $py scripts/forward_evidence_test.py insider register --baseline PATH_TO_SCORES --context PATH_TO_CONTEXT_SCORES
& $py scripts/forward_evidence_test.py followup register --context PATH_TO_FOLLOWUP_SCORES
& $py scripts/forward_evidence_test.py insider report
& $py scripts/forward_evidence_test.py followup report
```

These cohorts use separate TXT ledgers in `var/forward_insider_test/` and `var/forward_followup_test/`. Both use the same ITA benchmark and 1-, 5-, and 20-session windows. Historical insider rows can be scored for company profiles without entering the fresh forward cohort.
