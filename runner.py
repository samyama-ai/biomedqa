"""BiomedQA benchmark runner.

Loads scenario JSON files, executes Cypher queries against Samyama,
measures latency, and produces a results summary.

Usage:
    # Run all categories against a Samyama instance
    python runner.py --url http://localhost:8080

    # Run a single category
    python runner.py --url http://localhost:8080 --category drug_interactions

    # Dry run (validate scenarios only)
    python runner.py --dry-run

    # Output results to JSON
    python runner.py --url http://localhost:8080 --output results.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"

CATEGORY_FILES = [
    "drug_interactions.json",
    "side_effects.json",
    "pathway_membership.json",
    "cross_kg_federation.json",
    "polypharmacy_risk.json",
    "drug_classification.json",
    "adverse_events.json",
]


def load_scenarios(category: str | None = None) -> list[dict]:
    """Load scenario definitions from JSON files."""
    scenarios: list[dict] = []
    files = CATEGORY_FILES
    if category:
        target = f"{category}.json"
        files = [f for f in files if f == target]
        if not files:
            raise FileNotFoundError(f"No scenario file for category '{category}'")

    for fname in files:
        path = SCENARIOS_DIR / fname
        if path.exists():
            with open(path) as f:
                data = json.load(f)
                scenarios.extend(data)
        else:
            print(f"[WARN] Scenario file not found: {path}")

    return scenarios


def run_cypher(client, cypher: str, tenant: str = "default") -> tuple[list[dict], float]:
    """Execute a Cypher query and return (results, latency_ms).

    client can be a SamyamaClient or an HTTP URL string.
    """
    if isinstance(client, str):
        return _run_cypher_http(client, cypher, tenant)
    t0 = time.time()
    try:
        result = client.query(cypher, tenant)
        latency = (time.time() - t0) * 1000
        rows = [dict(zip(result.columns, row)) for row in result.records]
        return rows, latency
    except Exception as exc:
        latency = (time.time() - t0) * 1000
        return [{"error": str(exc)}], latency


def _run_cypher_http(url: str, cypher: str, tenant: str = "default") -> tuple[list[dict], float]:
    """Execute Cypher via HTTP API."""
    import requests
    t0 = time.time()
    try:
        resp = requests.post(
            f"{url.rstrip('/')}/api/query",
            json={"query": cypher, "tenant": tenant},
            timeout=30,
        )
        latency = (time.time() - t0) * 1000
        data = resp.json()
        if "error" in data:
            return [{"error": data["error"]}], latency
        columns = data.get("columns", [])
        records = data.get("records", [])
        rows = [dict(zip(columns, row)) for row in records]
        return rows, latency
    except Exception as exc:
        latency = (time.time() - t0) * 1000
        return [{"error": str(exc)}], latency


def evaluate_result(scenario: dict, rows: list[dict]) -> dict:
    """Evaluate whether query results match expected output."""
    expected = scenario.get("expected_output_contains", [])
    if not expected:
        # No specific expected values — just check non-empty
        passed = len(rows) > 0 and "error" not in rows[0]
        return {"passed": passed, "reason": "non-empty check"}

    # Flatten all values in rows to strings for containment check
    all_values = set()
    for row in rows:
        for v in row.values():
            if v is not None:
                all_values.add(str(v))

    missing = [e for e in expected if not any(e in v for v in all_values)]
    passed = len(missing) == 0 and "error" not in (rows[0] if rows else {})
    return {
        "passed": passed,
        "missing": missing,
        "reason": "containment check",
    }


def run_benchmark(
    client,
    scenarios: list[dict],
    tenant: str = "default",
) -> list[dict]:
    """Run all scenarios and collect results."""
    results = []
    for scenario in scenarios:
        sid = scenario["id"]
        cypher = scenario["cypher"]
        category = scenario["category"]
        difficulty = scenario.get("difficulty", "unknown")

        rows, latency = run_cypher(client, cypher, tenant)
        eval_result = evaluate_result(scenario, rows)

        result = {
            "id": sid,
            "category": category,
            "difficulty": difficulty,
            "question": scenario["question"],
            "passed": eval_result["passed"],
            "latency_ms": round(latency, 2),
            "row_count": len(rows),
            "reason": eval_result.get("reason", ""),
            "missing": eval_result.get("missing", []),
            "kgs_required": scenario.get("kgs_required", []),
        }
        results.append(result)

        status = "PASS" if result["passed"] else "FAIL"
        print(f"  [{status}] {sid} ({latency:.0f}ms) — {scenario['question'][:60]}")

    return results


def print_summary(results: list[dict]):
    """Print a summary table of results."""
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed
    avg_latency = sum(r["latency_ms"] for r in results) / total if total else 0

    print(f"\n{'=' * 60}")
    print(f"BiomedQA Benchmark — Results Summary")
    print(f"{'=' * 60}")
    print(f"  Total:    {total}")
    print(f"  Passed:   {passed} ({100*passed/total:.0f}%)")
    print(f"  Failed:   {failed}")
    print(f"  Avg lat:  {avg_latency:.1f} ms")

    # Per-category breakdown
    categories = {}
    for r in results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = {"total": 0, "passed": 0, "latencies": []}
        categories[cat]["total"] += 1
        if r["passed"]:
            categories[cat]["passed"] += 1
        categories[cat]["latencies"].append(r["latency_ms"])

    print(f"\n{'Category':<25s} {'Pass':>6s} {'Total':>6s} {'Pct':>6s} {'Avg ms':>8s}")
    print(f"{'─' * 55}")
    for cat, stats in sorted(categories.items()):
        pct = 100 * stats["passed"] / stats["total"]
        avg = sum(stats["latencies"]) / len(stats["latencies"])
        print(f"  {cat:<23s} {stats['passed']:>5d} {stats['total']:>5d} {pct:>5.0f}% {avg:>7.1f}")
    print(f"{'=' * 60}\n")

    # Failures detail
    failures = [r for r in results if not r["passed"]]
    if failures:
        print("Failures:")
        for f in failures:
            print(f"  {f['id']}: {f['question'][:70]}")
            if f.get("missing"):
                print(f"    Missing: {f['missing']}")


def main():
    parser = argparse.ArgumentParser(description="BiomedQA benchmark runner")
    parser.add_argument("--url", default=None, help="Samyama server URL")
    parser.add_argument("--tenant", default="default", help="Graph tenant")
    parser.add_argument("--category", default=None, help="Run single category")
    parser.add_argument("--dry-run", action="store_true", help="Validate scenarios only")
    parser.add_argument("--output", default=None, help="Output results JSON file")
    args = parser.parse_args()

    scenarios = load_scenarios(args.category)
    print(f"Loaded {len(scenarios)} scenarios")

    if args.dry_run:
        for s in scenarios:
            print(f"  {s['id']}: {s['question'][:60]} [{s['category']}]")
        print(f"\n{len(scenarios)} scenarios validated.")
        return

    if args.url:
        # Use HTTP API directly (no SDK dependency)
        client = args.url
    else:
        from samyama import SamyamaClient
        client = SamyamaClient.embedded()

    print(f"\nRunning {len(scenarios)} scenarios...\n")
    results = run_benchmark(client, scenarios, args.tenant)
    print_summary(results)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"Results written to {args.output}")


if __name__ == "__main__":
    main()
