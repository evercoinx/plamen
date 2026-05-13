"""SARIF 2.1.0 merge harness for Plamen L1 T2 multi-scoped-run composition.

## Problem

T2 (whole-client feature audit) runs Plamen L1 once per subsystem scope
(see plamen_l1/scopes/go-ethereum.json). Each run produces its own
Opengrep SARIF output + depth-agent findings (also rendered as SARIF).
Merging them requires:

1. **Deterministic dedup** — the same vulnerability may be flagged by
   two overlapping scopes (e.g. `eth/protocols/snap` appears in both
   "p2p" and "state-sync" scopes in the go-ethereum manifest). Dedup
   key: (rule_id, artifact uri, start_line, end_line).
2. **Scope traceability** — the merged report must record which scope
   surfaced each finding so a reviewer can trace the run back.
3. **Severity consolidation** — same-key duplicates with different
   severities keep the higher level (SARIF `error` > `warning` > `note` > `none`).
4. **Per-run tool driver preservation** — SARIF allows N runs in one
   file; each run keeps its own `tool.driver` so the merged report
   traces every finding back to the tool + scope that produced it.

## Why not `sarif-sdk` multitool?

The Microsoft sarif-sdk has a `sarif merge` command but it is a .NET
tool (adds a dotnet dependency just for merging), and it does not
semantically dedupe — it concatenates runs and rule lists.

## Why not GitHub code scanning?

GitHub's July 2025 policy rejects multi-run SARIF files with the same
tool + automationDetails.id. We set distinct `automationDetails.id`
per scope so the merged file remains uploadable if anyone wants to.

## Usage

    from pathlib import Path
    from plamen_l1.sarif_merge import merge_scoped_runs

    runs = [
        (Path("out/plamen_l1_p2p.sarif"),      "p2p"),
        (Path("out/plamen_l1_mempool.sarif"),  "mempool"),
        (Path("out/plamen_l1_consensus.sarif"),"consensus"),
    ]
    stats = merge_scoped_runs(runs, Path("out/plamen_l1_merged.sarif"))
    print(stats)  # MergeStats(total_in=83, unique_out=71, duplicates=12, ...)

## CLI

    python -m plamen_l1.sarif_merge \\
        out/plamen_l1_merged.sarif \\
        out/plamen_l1_p2p.sarif:p2p \\
        out/plamen_l1_mempool.sarif:mempool \\
        out/plamen_l1_consensus.sarif:consensus
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence, Tuple


SARIF_VERSION = "2.1.0"
SARIF_SCHEMA_URL = (
    "https://docs.oasis-open.org/sarif/sarif/v2.1.0/csd01/"
    "schemas/sarif-schema-2.1.0.json"
)

# SARIF result severity ordering (higher index = higher severity)
_LEVEL_ORDER = {"none": 0, "note": 1, "warning": 2, "error": 3}


@dataclass
class MergeStats:
    total_in: int = 0
    unique_out: int = 0
    duplicates: int = 0
    per_scope_counts: Dict[str, int] = field(default_factory=dict)
    duplicate_samples: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_in": self.total_in,
            "unique_out": self.unique_out,
            "duplicates": self.duplicates,
            "per_scope_counts": self.per_scope_counts,
            "duplicate_samples": self.duplicate_samples,
        }


DedupKey = Tuple[str, str, int, int]


def dedup_key(result: dict) -> DedupKey:
    """Compute the deduplication key for a SARIF result.

    (rule_id, artifact uri, start_line, end_line)

    This matches how Trivy and Checkov track locations internally and
    collapses the "same rule flagged same line twice because two scoped
    runs overlapped on a boundary file" case.
    """
    rule_id = result.get("ruleId", "")
    locations = result.get("locations") or [{}]
    loc = locations[0].get("physicalLocation") or {}
    artifact = (loc.get("artifactLocation") or {}).get("uri", "")
    region = loc.get("region") or {}
    start = region.get("startLine", 0) or 0
    end = region.get("endLine", start) or start
    return (rule_id, artifact, int(start), int(end))


def _higher_level(a: str, b: str) -> str:
    return a if _LEVEL_ORDER.get(a, 0) >= _LEVEL_ORDER.get(b, 0) else b


def merge_scoped_runs(
    runs: Sequence[Tuple[Path, str]],
    output: Path,
) -> MergeStats:
    """Merge N scoped SARIF runs into one deterministically-deduped file.

    Args:
        runs: sequence of (sarif_file_path, scope_name) tuples. Scope
              name is a short label like "p2p" or "consensus" that
              matches plamen_l1/scopes/*.json keys.
        output: path to write the merged SARIF file.

    Returns:
        MergeStats with per-scope counts and duplicate samples.
    """
    merged = {
        "version": SARIF_VERSION,
        "$schema": SARIF_SCHEMA_URL,
        "runs": [],
    }
    seen: Dict[DedupKey, dict] = {}
    stats = MergeStats()

    for run_path, scope in runs:
        if not run_path.exists():
            # Record and continue — absent runs are not an error in T2 because
            # some scopes may have produced no findings.
            stats.per_scope_counts[scope] = 0
            continue

        try:
            doc = json.loads(run_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid SARIF JSON in {run_path}: {exc}") from exc

        scope_count_in = 0
        scope_count_unique = 0

        for run in doc.get("runs", []):
            # Preserve the tool driver verbatim, stamp scope metadata.
            merged_run = {
                "tool": run.get("tool", {"driver": {"name": "unknown"}}),
                "automationDetails": {
                    "id": f"plamen-l1/{scope}",
                },
                "properties": {
                    "plamen_scope": scope,
                    "plamen_source_file": str(run_path),
                },
                "results": [],
            }

            for result in run.get("results", []):
                stats.total_in += 1
                scope_count_in += 1
                key = dedup_key(result)

                if key in seen:
                    stats.duplicates += 1
                    existing = seen[key]
                    # Severity consolidation: keep the higher level.
                    existing_level = existing.get("level", "warning")
                    new_level = result.get("level", "warning")
                    existing["level"] = _higher_level(existing_level, new_level)
                    # Track which scopes saw this finding.
                    extra = existing.setdefault("properties", {}).setdefault(
                        "additionalScopes", []
                    )
                    if scope not in extra:
                        extra.append(scope)
                    # Capture a sample of duplicates (for reporting visibility).
                    if len(stats.duplicate_samples) < 5:
                        stats.duplicate_samples.append({
                            "key": list(key),
                            "absorbing_scope": existing.get("properties", {}).get(
                                "plamen_scope", "?"
                            ),
                            "duplicate_scope": scope,
                        })
                    continue

                # New finding — stamp its primary scope, record, retain.
                result.setdefault("properties", {})["plamen_scope"] = scope
                seen[key] = result
                merged_run["results"].append(result)
                scope_count_unique += 1
                stats.unique_out += 1

            merged["runs"].append(merged_run)

        stats.per_scope_counts[scope] = scope_count_in

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(merged, indent=2), encoding="utf-8")

    return stats


def _cli(argv: List[str]) -> int:
    if len(argv) < 3:
        sys.stderr.write(
            "Usage:\n"
            "  python -m plamen_l1.sarif_merge <output.sarif> <input1.sarif:scope1> [input2.sarif:scope2 ...]\n"
        )
        return 2

    output = Path(argv[1])
    runs: List[Tuple[Path, str]] = []
    for arg in argv[2:]:
        if ":" not in arg:
            sys.stderr.write(f"Error: expected 'path:scope', got {arg!r}\n")
            return 2
        # Windows absolute paths contain a ':' after the drive letter. Split
        # from the right so C:\foo.sarif:scope parses as (C:\foo.sarif, scope).
        path_str, scope = arg.rsplit(":", 1)
        runs.append((Path(path_str), scope))

    stats = merge_scoped_runs(runs, output)
    print(json.dumps(stats.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
