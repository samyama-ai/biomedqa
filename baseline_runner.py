"""BiomedQA baseline runner — GPT-4 standalone + Text-to-Cypher.

Runs the same 40 BiomedQA questions through:
1. GPT-4 standalone (no database access)
2. Text-to-Cypher (GPT-4 generates Cypher, Samyama executes)

Usage:
    python baseline_runner.py --url http://localhost:8080 --output results_baselines.json
    python baseline_runner.py --url http://localhost:8080 --mode text-to-cypher
    python baseline_runner.py --url http://localhost:8080 --mode standalone
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import requests

try:
    from openai import OpenAI
except ImportError:
    print("pip install openai")
    raise

SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"
CATEGORY_FILES = [
    "drug_interactions.json", "side_effects.json", "pathway_membership.json",
    "cross_kg_federation.json", "polypharmacy_risk.json",
    "drug_classification.json", "adverse_events.json",
]

# Schema summary for text-to-Cypher prompting
SCHEMA_SUMMARY = """
Graph schema (3 KGs loaded in single instance):

DRUG INTERACTIONS KG:
- (:Drug {drugbank_id, name, cas_number}) -[:INTERACTS_WITH_GENE {interaction_type, source}]-> (:Gene {gene_name})
- (:Drug) -[:HAS_SIDE_EFFECT]-> (:SideEffect {meddra_id, name})
- (:Drug) -[:HAS_INDICATION {method}]-> (:Indication {meddra_id, name})

PATHWAYS KG:
- (:Protein {uniprot_id, name}) -[:PARTICIPATES_IN]-> (:Pathway {name, pathway_id})
- (:Protein) -[:INTERACTS_WITH {combined_score}]-> (:Protein)
- (:Protein) -[:ANNOTATED_WITH]-> (:GOTerm {go_id, name, namespace})
- (:Pathway) -[:CHILD_OF]-> (:Pathway)

CLINICAL TRIALS KG:
- (:ClinicalTrial {nct_id, phase, condition}) -[:TESTS]-> (:Intervention {name})
- (:ClinicalTrial) -[:STUDIES]-> (:Condition {name})
- (:ClinicalTrial) -[:REPORTED]-> (:AdverseEvent {term})

Bridge properties: Gene.gene_name = Protein.name; Drug.name = Intervention.name
"""

FEW_SHOT_EXAMPLES = """
Example 1:
Question: What genes does Metformin interact with?
Cypher: MATCH (d:Drug {name: 'Metformin'})-[i:INTERACTS_WITH_GENE]->(g:Gene) RETURN g.gene_name, i.interaction_type

Example 2:
Question: Which proteins participate in the Apoptosis pathway?
Cypher: MATCH (p:Protein)-[:PARTICIPATES_IN]->(pw:Pathway {name: 'Apoptosis'}) RETURN p.uniprot_id LIMIT 10

Example 3:
Question: What shared side effects do two drugs have?
Cypher: MATCH (d1:Drug {name: 'DrugA'})-[:HAS_SIDE_EFFECT]->(se:SideEffect)<-[:HAS_SIDE_EFFECT]-(d2:Drug {name: 'DrugB'}) RETURN se.name
"""


def load_scenarios() -> list[dict]:
    scenarios = []
    for fname in CATEGORY_FILES:
        path = SCENARIOS_DIR / fname
        if path.exists():
            with open(path) as f:
                scenarios.extend(json.load(f))
    return scenarios


def run_cypher_http(url: str, cypher: str) -> tuple[list[dict], float]:
    t0 = time.time()
    try:
        resp = requests.post(
            f"{url.rstrip('/')}/api/query",
            json={"query": cypher}, timeout=30,
        )
        latency = (time.time() - t0) * 1000
        data = resp.json()
        if "error" in data:
            return [{"error": data["error"]}], latency
        columns = data.get("columns", [])
        records = data.get("records", [])
        return [dict(zip(columns, row)) for row in records], latency
    except Exception as exc:
        return [{"error": str(exc)}], (time.time() - t0) * 1000


def evaluate_result(scenario: dict, rows: list[dict]) -> dict:
    expected = scenario.get("expected_output_contains", [])
    if not expected:
        passed = len(rows) > 0 and "error" not in rows[0]
        return {"passed": passed, "reason": "non-empty check"}
    all_values = set()
    for row in rows:
        for v in row.values():
            if v is not None:
                all_values.add(str(v))
    missing = [e for e in expected if not any(e in v for v in all_values)]
    passed = len(missing) == 0 and "error" not in (rows[0] if rows else {})
    return {"passed": passed, "missing": missing}


def evaluate_text(scenario: dict, text: str) -> dict:
    """Evaluate standalone GPT-4 text response against expected values."""
    expected = scenario.get("expected_output_contains", [])
    if not expected:
        passed = len(text.strip()) > 10
        return {"passed": passed, "reason": "non-empty text check"}
    missing = [e for e in expected if e.lower() not in text.lower()]
    passed = len(missing) == 0
    return {"passed": passed, "missing": missing}


# ── GPT-4 Standalone ────────────────────────────────────────────────────

def run_standalone(client: OpenAI, scenarios: list[dict]) -> list[dict]:
    """GPT-4 answers from training data, no database."""
    results = []
    for s in scenarios:
        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a pharmacology expert. Answer precisely and concisely."},
                    {"role": "user", "content": s["question"]},
                ],
                max_tokens=500,
                temperature=0,
            )
            latency = (time.time() - t0) * 1000
            text = resp.choices[0].message.content
            tokens = resp.usage.total_tokens
            ev = evaluate_text(s, text)
        except Exception as exc:
            latency = (time.time() - t0) * 1000
            text = str(exc)
            tokens = 0
            ev = {"passed": False, "missing": ["error"]}

        status = "PASS" if ev["passed"] else "FAIL"
        print(f"  [{status}] {s['id']} ({latency:.0f}ms, {tokens}tok) — {s['question'][:55]}")

        results.append({
            "id": s["id"], "category": s["category"],
            "question": s["question"], "approach": "standalone",
            "passed": ev["passed"], "latency_ms": round(latency, 1),
            "tokens": tokens, "missing": ev.get("missing", []),
        })
    return results


# ── Text-to-Cypher ──────────────────────────────────────────────────────

def run_text_to_cypher(client: OpenAI, url: str, scenarios: list[dict]) -> list[dict]:
    """GPT-4 generates Cypher, Samyama executes."""
    results = []
    for s in scenarios:
        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content":
                        "You are a Cypher query expert. Given a graph database schema and a question, "
                        "generate ONLY the Cypher query. No explanation, no markdown fences.\n\n"
                        f"SCHEMA:\n{SCHEMA_SUMMARY}\n\n"
                        f"EXAMPLES:\n{FEW_SHOT_EXAMPLES}"},
                    {"role": "user", "content": s["question"]},
                ],
                max_tokens=300,
                temperature=0,
            )
            gen_latency = (time.time() - t0) * 1000
            cypher = resp.choices[0].message.content.strip()
            tokens = resp.usage.total_tokens

            # Clean up — remove markdown fences if any
            cypher = cypher.replace("```cypher", "").replace("```", "").strip()

            # Execute generated Cypher
            rows, exec_latency = run_cypher_http(url, cypher)
            total_latency = gen_latency + exec_latency
            ev = evaluate_result(s, rows)

        except Exception as exc:
            total_latency = (time.time() - t0) * 1000
            tokens = 0
            cypher = ""
            ev = {"passed": False, "missing": ["error: " + str(exc)]}

        status = "PASS" if ev["passed"] else "FAIL"
        print(f"  [{status}] {s['id']} ({total_latency:.0f}ms, {tokens}tok) — {s['question'][:55]}")
        if not ev["passed"]:
            print(f"         Cypher: {cypher[:80]}")

        results.append({
            "id": s["id"], "category": s["category"],
            "question": s["question"], "approach": "text-to-cypher",
            "passed": ev["passed"], "latency_ms": round(total_latency, 1),
            "tokens": tokens, "cypher_generated": cypher,
            "missing": ev.get("missing", []),
        })
    return results


def print_summary(results: list[dict], approach: str):
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    avg_lat = sum(r["latency_ms"] for r in results) / total if total else 0
    avg_tok = sum(r.get("tokens", 0) for r in results) / total if total else 0
    print(f"\n{'='*60}")
    print(f"{approach}: {passed}/{total} ({100*passed/total:.0f}%) "
          f"avg={avg_lat:.0f}ms avg_tokens={avg_tok:.0f}")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="BiomedQA baseline runner")
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--mode", choices=["standalone", "text-to-cypher", "both"],
                        default="both")
    parser.add_argument("--output", default=None)
    parser.add_argument("--api-key", default=None, help="OpenAI API key (or set OPENAI_API_KEY)")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: Set OPENAI_API_KEY or pass --api-key")
        return

    client = OpenAI(api_key=api_key)
    scenarios = load_scenarios()
    print(f"Loaded {len(scenarios)} scenarios\n")

    all_results = []

    if args.mode in ("standalone", "both"):
        print("=== GPT-4 Standalone ===\n")
        standalone = run_standalone(client, scenarios)
        print_summary(standalone, "GPT-4 Standalone")
        all_results.extend(standalone)

    if args.mode in ("text-to-cypher", "both"):
        print("\n=== Text-to-Cypher ===\n")
        t2c = run_text_to_cypher(client, args.url, scenarios)
        print_summary(t2c, "Text-to-Cypher")
        all_results.extend(t2c)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(all_results, f, indent=2)
        print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    main()
