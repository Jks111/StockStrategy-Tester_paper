# Run the QuantLab worker on Windows

## Automatic paper portfolio

1. Start QuantLab on dev. Open Worker and find **Automated Alpaca paper portfolio**.
2. Review the fixed basket of AAPL, AMZN and SPY, the five strategy names, and the editable cash reserve. The displayed example assumes $100,000. Actual sizing uses Alpaca paper equity and cash. With the default reserve the initial combined entries at full size across all fifteen pairs could spend up to $36,000.
3. Click **Start automatic paper trading** to enable simulated broker orders. No market orders are submitted outside regular trading hours. Leave QuantLab running and keep the PC awake. The browser may be closed.
4. Check recent order statuses and fills in Worker and the Alpaca paper dashboard. Prices are observed periodically; stops, cash targets and session boundaries cannot guarantee a fill at the observed price. Both overnight strategies may own the same stock on Friday.
5. **Stop monitoring** stops future checks. It does not flatten positions or cancel previously submitted orders. After a server restart, review positions, set your desired reserve, and start again. If you sell on the Alpaca paper site, the worker waits for open orders to settle and checks confirmed sales against the change in shares. It records the broker fill price, assigning shared shares to the oldest open strategy entry first. A matched external sale blocks new entries in that ticker for the rest of its New York execution date. Remaining shares still follow their exit rules. An unexplained difference puts the affected ticker on hold and is retried. Preserve the SQLite database because it records strategy ownership.

## Command-line signal observation

The worker on dev observes a typed watchlist of up to 30 US tickers with the opening five-minute EMA strategy. It records decisions in SQLite. It cannot allocate the Alpaca $100,000 balance, place paper orders, or track fills. A decision is an observation, not a trade.

1. Open PowerShell in the project folder. Confirm the branch and available commands:

   ```powershell
   Set-Location "$HOME\Documents\ChatGPT\Trading bot demo"
   git branch --show-current
   .\.venv\Scripts\python.exe run.py paper-worker --help
   ```

   Expect `dev` and the options `--configure` and `--once`. The dashboard and its launcher are optional.

2. Use the Alpaca paper credentials you already saved in the local `.env` file. Keep the key ID and secret out of screenshots, terminal output, chat and Git. If a secret is exposed, revoke it in Alpaca and replace it locally.

3. Configure a watchlist. For example, to observe AAPL beginning November 19, 2026:

   ```powershell
   .\.venv\Scripts\python.exe run.py paper-worker --configure AAPL JPM XOM --start 2026-11-19 --feed iex
   ```

   Substitute the first US session date you want recorded. An earlier date can backfill historical decisions. The command prints a configuration version. Each configure command replaces the active watchlist. Include every ticker you want to keep. Each ticker resumes its own observations. IEX covers one exchange. SIP uses a 16-minute delay in this worker.

4. Process available completed bars once:

   ```powershell
   .\.venv\Scripts\python.exe run.py paper-worker --once
   ```

   Check `last_bar_end` for the newest processed time and its UTC offset. `decisions_added` counts both signals and no-signal bars. Zero can be normal outside market hours or when all available bars have already been recorded.

5. For repeated observation, keep a PowerShell window running this loop. Use Ctrl+C to stop. This exits on a worker error so you can read it.

   ```powershell
   while ($true) {
       .\.venv\Scripts\python.exe run.py paper-worker --once
       if ($LASTEXITCODE -ne 0) { break }
       Start-Sleep -Seconds 300
   }
   ```

   Keep the PC awake and connected during observation. After a restart, run the same loop again. The saved configuration and recorded bars survive closing PowerShell. Do not reset the start date or delete SQLite to resume.

6. To inspect actual decisions, open `var/research.sqlite` with a read-only SQLite viewer and run:

   ```sql
   SELECT c.symbol, d.config_version, d.session, d.bar_end, d.outcome, d.signal
   FROM paper_signal_decisions d JOIN paper_signal_configs c ON c.version=d.config_version
   ORDER BY config_version DESC, bar_end DESC
   LIMIT 20;
   ```

   A signal recorded during catch-up was computed from historical bars. Do not treat it as a current entry or an Alpaca fill. For a backup, stop the worker and dashboard, then copy the entire var folder. Avoid editing the decision tables manually.
