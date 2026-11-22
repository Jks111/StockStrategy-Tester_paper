"""Worker fill attribution and USD mark-to-bid performance for a single paper account."""

import json
from collections import defaultdict, deque
from decimal import ROUND_CEILING, ROUND_FLOOR
from decimal import Decimal as D

from .strategies import definition

CENT = D(".01")
# Alpaca direct retail US listed equities, schedule revised 2026-11-17.
SEC_RATE = D(".0000206")
CAT_RATE = D(".000003")


def allocate_fees(groups):
    """Round each fee per day/account, then apportion whole cents to strategies."""
    totals = defaultdict(D)
    for weights in groups.values():
        raw = sum(weights.values())
        if not raw:
            continue
        cents = (raw / CENT).to_integral_value(rounding=ROUND_CEILING)
        shares = {s: cents * amount / raw for s, amount in weights.items()}
        whole = {s: v.to_integral_value(rounding=ROUND_FLOOR) for s, v in shares.items()}
        extra = int(cents - sum(whole.values()))
        for s in sorted(shares, key=lambda s: (whole[s] - shares[s], s))[:extra]:
            whole[s] += 1
        for s, count in whole.items():
            totals[s] += CENT * count
    return totals


def performance(db, account, strategies, symbols):
    fills = db.execute(
        """
        SELECT o.*, (SELECT j.time FROM worker_journal j WHERE j.kind='broker'
            AND json_extract(j.context,'$.order_id')=o.cid
            AND CAST(json_extract(j.context,'$.filled_qty') AS REAL)>0
            ORDER BY j.id LIMIT 1) AS observed
        FROM paper_basket_orders o WHERE account=? AND CAST(qty AS REAL)>0 ORDER BY o.rowid
    """,
        (account,),
    )
    lots = defaultdict(deque)
    realized = defaultdict(D)
    sell_profits = {}
    groups = defaultdict(lambda: defaultdict(D))
    for row in fills:
        s, ticker = row["strategy"], row["symbol"]
        qty, price = D(row["qty"]), D(row["avg"])
        groups[row["day"], "cat"][s] += qty * CAT_RATE
        queue = lots[s, ticker]
        if row["side"] == "buy":
            queue.append([qty, price, row["observed"], row["day"]])
            continue
        groups[row["day"], "sec"][s] += qty * price * SEC_RATE
        profit = D(0)
        while qty > 0 and queue:
            matched = min(queue[0][0], qty)
            profit += matched * (price - queue[0][1])
            qty -= matched
            queue[0][0] -= matched
            if not queue[0][0]:
                queue.popleft()
        if qty:
            raise ValueError("Worker sell exceeds recorded strategy holdings")
        realized[s] += profit
        sell_profits[row["cid"]] = profit

    fees = allocate_fees(groups)
    marks = {}
    for symbol in symbols:
        row = db.execute(
            """
            SELECT json_extract(context,'$.quote') FROM worker_journal
            WHERE kind='decision' AND symbol=?
              AND json_extract(context,'$.quote.bp')>0
              AND json_extract(context,'$.quote.ap')>=json_extract(context,'$.quote.bp')
              AND json_extract(context,'$.quote.t') IS NOT NULL
            ORDER BY id DESC LIMIT 1
        """,
            (symbol,),
        ).fetchone()
        if row:
            marks[symbol] = json.loads(row[0])

    result = []
    for s in strategies:
        holdings = []
        unrealized = D(0)
        complete = True
        for symbol in symbols:
            for qty, price, opened, day in lots[s, symbol]:
                mark = marks.get(symbol)
                pnl = None
                if mark:
                    pnl = qty * (D(str(mark["bp"])) - price)
                    unrealized += pnl
                else:
                    complete = False
                holdings.append(
                    dict(
                        symbol=symbol,
                        qty=str(qty),
                        entry=float(price),
                        opened=opened,
                        day=day,
                        mark=mark,
                        pnl=float(pnl) if pnl is not None else None,
                    )
                )
        result.append(
            dict(
                strategy=s,
                name=definition(s)["name"],
                holdings=holdings,
                realized=float(realized[s]),
                unrealized=float(unrealized) if complete else None,
                fees=float(fees[s]),
                net=float(realized[s] + unrealized - fees[s]) if complete else None,
            )
        )
    return result, sell_profits
