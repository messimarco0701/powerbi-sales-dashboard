"""
Copies the synthetic CRM dataset from the sales-pipeline-analytics repo and adds
a monthly sales target per channel, which a real company would get from its budget.

Targets are synthetic too: last year's actuals for the same month and channel,
plus a growth ambition, rounded the way a finance team would round them.
For the first year there is no history, so actuals are perturbed a little.
"""

from __future__ import annotations

import random
import shutil
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "sales-pipeline-analytics" / "data"
DATA = ROOT / "data"
FILES = ["fact_deals.csv", "fact_stage_history.csv", "dim_company.csv", "dim_partner.csv", "dim_owner.csv"]
GROWTH_AMBITION = 1.18

rng = random.Random(11)


def build_targets(deals: pd.DataFrame) -> pd.DataFrame:
    won = deals[deals.is_won].copy()
    won["month"] = pd.to_datetime(won.close_date).dt.to_period("M").dt.to_timestamp()
    actual = won.groupby(["month", "channel"]).amount.sum()

    rows = []
    for month in pd.date_range("2024-01-01", "2026-12-01", freq="MS"):
        for channel in sorted(deals.channel.unique()):
            if month.year == 2024:
                base = actual.get((month, channel), 0) * rng.uniform(0.9, 1.15)
            else:
                base = actual.get((month - pd.DateOffset(years=1), channel), 0) * GROWTH_AMBITION
            rows.append({"month": month.date().isoformat(), "channel": channel,
                         "target": max(round(base, -3), 1000)})
    return pd.DataFrame(rows)


def main() -> None:
    DATA.mkdir(exist_ok=True)
    for name in FILES:
        shutil.copy(SOURCE / name, DATA / name)
    deals = pd.read_csv(DATA / "fact_deals.csv")
    targets = build_targets(deals)
    targets.to_csv(DATA / "targets.csv", index=False)
    print(f"Copied {len(FILES)} files, wrote {len(targets)} target rows")


if __name__ == "__main__":
    main()
