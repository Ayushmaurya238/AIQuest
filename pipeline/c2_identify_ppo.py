"""C2: classify plan links with explicit network and market evidence."""

from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from pipeline.common import ensure_dirs, read_csv, write_json

LOG = logging.getLogger(__name__)
PPO_RE = re.compile(r"\bPPO\b", re.I)
NON_PPO_RE = re.compile(r"\b(HMO|EPO|POS)\b|POINT\s+OF\s+SERVICE", re.I)
GROUP_MARKETS = {"group", "small_group", "large_group"}


def classify(row: pd.Series, target_state: str = "CA") -> dict[str, Any]:
    name = str(row.get("plan_name", "") or "")
    market = str(row.get("market_type", "") or "").strip().lower()
    state = str(row.get("state", "") or "").strip().upper()
    has_ppo = bool(PPO_RE.search(name))
    hard_non_ppo = sorted(set(m.group(1).upper() if m.lastindex else m.group(0).upper() for m in NON_PPO_RE.finditer(name)))
    conflict = has_ppo and bool(hard_non_ppo)
    out_of_area = bool(re.search(r"\bOUT\s+OF\s+AREA\b|\bNON[- ]?CA\b", name, re.I))

    if market not in GROUP_MARKETS:
        status, reason = "excluded_scope_market", f"market_type={market or 'missing'}; commercial group scope requires an explicit group market"
    elif state != target_state:
        if not state:
            status, reason = "review_scope_state", "State is not explicit in the plan ID, plan name, or rate-file URL."
        else:
            status, reason = "excluded_scope_state", f"state={state}; target={target_state}"
    elif conflict:
        status, reason = "review_network_conflict", "Plan name contains both PPO and a hard HMO/EPO/POS signal."
    elif out_of_area:
        status, reason = "review_geography", "Out-of-area wording needs provider-market confirmation."
    elif has_ppo:
        status, reason = "include_explicit_ppo", "Explicit PPO token, explicit group market, and California state evidence."
    elif hard_non_ppo:
        status, reason = "excluded_non_ppo", f"Explicit non-PPO signal: {','.join(hard_non_ppo)}"
    else:
        status, reason = "review_network_ambiguous", "No explicit PPO/HMO/EPO/POS network token; no inference from Choice, HDHP, or brand."
    return {
        "network_token_ppo": has_ppo,
        "network_token_non_ppo": "|".join(hard_non_ppo),
        "network_token_conflict": conflict,
        "market_in_scope": market in GROUP_MARKETS,
        "state_in_scope": state == target_state,
        "out_of_area_wording": out_of_area,
        "decision": status,
        "decision_reason": reason,
    }


def run(config: dict[str, Any], logger: logging.Logger | None = None) -> pd.DataFrame:
    logger = logger or LOG
    dirs = ensure_dirs(config)
    links = read_csv(dirs["processed"] / "plan_file_links.csv")
    target_state = str(config.get("filters", {}).get("state", "CA")).upper()
    signals = links.apply(lambda row: classify(row, target_state), axis=1, result_type="expand")
    decisions = pd.concat([links, signals], axis=1)
    decisions.to_csv(dirs["processed"] / "plan_classification.csv", index=False)

    stats = decisions.groupby("file_id", as_index=False).agg(
        rate_file_url=("rate_file_url", "first"),
        brands=("brand", lambda x: "|".join(sorted(set(x)))),
        linked_plan_count=("link_id", "count"),
        unique_plan_count=("plan_name", "nunique"),
        unique_plan_id_count=("plan_id", "nunique"),
        explicit_ppo_plan_links=("network_token_ppo", "sum"),
        clean_ppo_plan_links=("decision", lambda x: int((x == "include_explicit_ppo").sum())),
        ambiguous_plan_links=("decision", lambda x: int(x.str.startswith("review_").sum())),
        excluded_plan_links=("decision", lambda x: int(x.str.startswith("excluded_").sum())),
        all_decisions=("decision", lambda x: "|".join(sorted(set(x)))),
    )
    stats["clean_ppo_unique_plan_count"] = [
        decisions.loc[
            (decisions["file_id"] == file_id) & (decisions["decision"] == "include_explicit_ppo"),
            ["brand", "plan_name", "plan_id"],
        ].drop_duplicates().shape[0]
        for file_id in stats["file_id"]
    ]
    mixed_flags: list[bool] = []
    selected_flags: list[bool] = []
    network_status: list[str] = []
    for _, row in stats.iterrows():
        subset = decisions[decisions["file_id"] == row["file_id"]]
        included = subset[subset["decision"] == "include_explicit_ppo"]
        selected = not included.empty
        # One URL may be referenced by PPO and non-PPO plan links. Keep that fact visible.
        mixed = (
            selected
            and (
                bool((subset["decision"] == "excluded_non_ppo").any())
                or bool(subset["decision"].str.startswith("review_").any())
                or bool((subset["decision"] == "include_explicit_ppo").sum() < len(subset))
            )
        )
        selected_flags.append(selected)
        mixed_flags.append(mixed)
        network_status.append("shared_mixed_plan_file" if mixed else ("ppo_linked_file" if selected else "not_selected"))
    stats["selected_for_extraction"] = selected_flags
    stats["mixed_plan_file"] = mixed_flags
    stats["rate_network_attribution"] = network_status
    stats["target_state"] = target_state
    stats.to_csv(dirs["processed"] / "selected_ppo_files.csv", index=False)

    counts = Counter(decisions["decision"])
    selected = stats[stats["selected_for_extraction"]]
    manifest = {
        "stage": "C2",
        "status": "complete",
        "target_state": target_state,
        "plan_file_links_classified": int(len(decisions)),
        "decision_counts": dict(counts),
        "unique_explicit_ppo_plans": int(
            decisions.loc[decisions["decision"] == "include_explicit_ppo", ["brand", "plan_name", "plan_id"]]
            .drop_duplicates()
            .shape[0]
        ),
        "unique_files_considered": int(stats["file_id"].nunique()),
        "files_selected_for_extraction": int(len(selected)),
        "selected_mixed_plan_files": int(selected["mixed_plan_file"].sum()) if len(selected) else 0,
        "selected_files": selected[["file_id", "rate_file_url", "clean_ppo_plan_links", "clean_ppo_unique_plan_count", "mixed_plan_file", "rate_network_attribution"]].to_dict(orient="records"),
        "logic": "Only an explicit PPO token plus explicit group market plus California evidence is automatically included. Contradictory, out-of-area, unknown, and ambiguous cases are separately reviewed; Choice and HDHP alone are not treated as PPO.",
    }
    write_json(dirs["processed"] / "c2_manifest.json", manifest)
    logger.info(
        "C2 complete: %d explicit PPO plan links; %d unique rate files selected (%d mixed-link files)",
        int((decisions["decision"] == "include_explicit_ppo").sum()),
        len(selected),
        int(selected["mixed_plan_file"].sum()) if len(selected) else 0,
    )
    return decisions
