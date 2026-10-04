"""Side-by-side comparison of our backtest tables with the playbook's published Tables 1, 4 and 5."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

REFERENCE = Path(__file__).with_name("playbook_reference.yaml")


def load_reference(path: Path = REFERENCE) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def compare(ours: dict[str, pd.DataFrame], reference: dict | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Return (comparison rows, notes). Rows: table, key, metric, playbook, ours, difference."""
    reference = load_reference() if reference is None else reference
    rows, notes = [], []
    for name, spec in reference.items():
        spec = spec or {}
        entries = spec.get("rows") or []
        if not entries:
            notes.append(f"{name}: no playbook values entered in playbook_reference.yaml; nothing to compare. "
                         f"Our closest table is '{spec.get('compare_to')}'.")
            continue
        table = ours.get(spec.get("compare_to"))
        key_col = spec.get("key_column")
        for e in entries:
            mine = None
            if table is not None and not table.empty and key_col in table.columns and e["metric"] in table.columns:
                hit = table[table[key_col].astype(str) == str(e["key"])]
                if not hit.empty:
                    mine = hit.iloc[0][e["metric"]]
            ref = e.get("value")
            rows.append({"table": name, "title": spec.get("title"), "key": e["key"], "metric": e["metric"],
                         "playbook": ref, "ours": mine,
                         "difference": (mine - ref) if (mine is not None and ref is not None and not pd.isna(mine)) else None,
                         "note": "" if mine is not None else "row/metric not found in our table"})
    return pd.DataFrame(rows, columns=["table", "title", "key", "metric", "playbook", "ours", "difference", "note"]), notes
