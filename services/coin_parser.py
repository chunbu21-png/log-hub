"""Self-contained parser for OKX 3in1 live log snapshots."""
from __future__ import annotations

SHEET_KEY = "1hdwoh-Tc5LDVOsOcaJnNx00vKkqNOiU3E_ikW45yKiQ"


def parse_live_rows(lines: list[str]) -> list[dict]:
    rows: list[dict] = []
    for i, line in enumerate(lines):
        if not (line.startswith("=== OKX_3in1") and "DRY_RUN=False" in line):
            continue

        utc = free = equity = None
        positions: list[str] = []
        for detail in lines[i : i + 40]:
            if detail.startswith("UTC now:"):
                utc = detail.replace("UTC now:", "", 1).strip()
            elif detail.startswith("positions:"):
                left = detail.find("[")
                right = detail.find("]")
                inner = detail[left + 1 : right].strip() if left >= 0 and right > left else ""
                if inner:
                    positions = [value.strip().strip("'\"") for value in inner.split(",")]
            elif detail.startswith("USDT free"):
                for part in detail.replace("USDT ", "", 1).split():
                    if part.startswith("free~"):
                        free = float(part.split("~", 1)[1])
                    elif part.startswith("equity~"):
                        equity = float(part.split("~", 1)[1])
                break

        if utc and equity is not None:
            rows.append(
                {
                    "utc": utc,
                    "free": free,
                    "equity": equity,
                    "n_pos": len(positions),
                    "pos": positions,
                }
            )

    unique = {row["utc"]: row for row in rows}
    return sorted(unique.values(), key=lambda row: row["utc"])


def apply_cashflow_adjust(
    rows: list[dict],
    jump_threshold: float = 50.0,
    deploy_drop_threshold: float = 5.0,
    ghost_release_threshold: float = 15.0,
) -> tuple[list[dict], list[dict]]:
    """Remove deposits and position-state artifacts from the equity series."""
    adjusted: list[dict] = []
    events: list[dict] = []
    cumulative_inflow = 0.0
    cumulative_deploy = 0.0
    previous = None

    for row in rows:
        current = dict(row)
        if previous is not None:
            free_delta = float(row["free"]) - float(previous["free"])
            equity_delta = float(row["equity"]) - float(previous["equity"])

            if free_delta >= jump_threshold and row["n_pos"] >= previous["n_pos"]:
                cumulative_inflow += free_delta
                events.append(
                    {
                        "utc": row["utc"],
                        "amount": round(free_delta, 2),
                        "kind": "deposit_like",
                    }
                )
            elif (
                row["n_pos"] == previous["n_pos"]
                and row["n_pos"] > 0
                and free_delta >= ghost_release_threshold
                and equity_delta >= ghost_release_threshold * 0.8
            ):
                cumulative_inflow += free_delta
                events.append(
                    {
                        "utc": row["utc"],
                        "amount": round(free_delta, 2),
                        "kind": "ghost_flat_release",
                    }
                )
            elif (
                row["n_pos"] < previous["n_pos"]
                and equity_delta <= -ghost_release_threshold
                and abs(free_delta) < ghost_release_threshold * 0.5
            ):
                cumulative_deploy += -equity_delta
                events.append(
                    {
                        "utc": row["utc"],
                        "amount": round(-equity_delta, 2),
                        "kind": "ghost_state_clear",
                        "n_pos": f"{previous['n_pos']}->{row['n_pos']}",
                    }
                )

            if row["n_pos"] > previous["n_pos"] and equity_delta <= -deploy_drop_threshold:
                cumulative_deploy += -equity_delta
                events.append(
                    {
                        "utc": row["utc"],
                        "amount": round(-equity_delta, 2),
                        "kind": "deploy_cliff",
                        "n_pos": f"{previous['n_pos']}->{row['n_pos']}",
                    }
                )

        current["cum_inflow"] = round(cumulative_inflow, 2)
        current["cum_deploy"] = round(cumulative_deploy, 2)
        current["adj_equity"] = round(
            float(row["equity"]) - cumulative_inflow + cumulative_deploy, 2
        )
        adjusted.append(current)
        previous = row

    return adjusted, events
