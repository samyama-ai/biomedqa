# BiomedQA Benchmark

40 pharmacology questions over three federated biomedical knowledge graphs, designed to evaluate AI agent approaches for domain-specific data access.

> Part of the **Samyama** ecosystem — exercises three biomedical KGs via the graph engine at [samyama-ai/samyama-graph](https://github.com/samyama-ai/samyama-graph).
> Benchmark framework + ground-truth set; the underlying KG data lives in [pubmed-kg](https://github.com/samyama-ai/pubmed-kg), [clinicaltrials-kg](https://git.samyama.ai/Samyama.ai/clinicaltrials-kg), and [druginteractions-kg](https://git.samyama.ai/Samyama.ai/druginteractions-kg).

## Key Results

| Approach | Accuracy | Avg Latency | How |
|----------|----------|-------------|-----|
| **MCP tools** | **39/40 (98%)** | **920ms** | Pre-authored Cypher templates, deterministic |
| Text-to-Cypher (NLQ) | 34/40 (85%) | 1,846ms | Schema-aware NLQ endpoint (GPT-4o) |
| GPT-4o standalone | 30/40 (75%) | 2,805ms | Answers from training data, no database |

### Error Categorization

| Result Category | MCP | Text-to-Cypher | Standalone |
|----------------|-----|----------------|-----------|
| Correct answer | 39 | 34 | 30 |
| Correct empty (no data exists) | 1 | 1 | 0 |
| Schema mismatch | 0 | 3 | — |
| Data mismatch (exact vs CONTAINS) | 0 | 1 | — |
| Inline property variable | 0 | 1 | — |
| Hallucinated answer | — | — | 5 |
| Missing precision | — | — | 5 |

**Key finding:** MCP tools have **zero schema errors** — the Cypher templates are authored against the actual schema. Text-to-Cypher fails on cross-KG joins and schema hallucinations. GPT-4o standalone fails on precision-requiring questions.

## Knowledge Graphs Required

| KG | Nodes | Edges | Sources | Snapshot |
|----|------:|------:|---------|----------|
| Pathways | 118,686 | 834,785 | Reactome, STRING, GO, WikiPathways, UniProt | [kg-snapshots-v3](https://github.com/samyama-ai/samyama-graph/releases/tag/kg-snapshots-v3) |
| Drug Interactions | 32,726 | 191,970 | DrugBank CC0, DGIdb, SIDER | [kg-snapshots-v4](https://github.com/samyama-ai/samyama-graph/releases/tag/kg-snapshots-v4) |
| Clinical Trials | 7,774,446 | 26,973,997 | ClinicalTrials.gov, MeSH, RxNorm, OpenFDA, PubMed | [kg-snapshots-v1](https://github.com/samyama-ai/samyama-graph/releases/tag/kg-snapshots-v1) |

## Question Categories (40 total)

| Category | Count | KGs Used | Difficulty |
|----------|------:|----------|-----------|
| Drug interactions | 8 | Drug Interactions | Easy-Medium |
| Side effect lookup | 6 | Drug Interactions | Easy-Hard |
| Pathway membership | 6 | Pathways | Easy-Medium |
| Cross-KG federation | 8 | Drug Int. + Pathways + Clinical Trials | Hard |
| Polypharmacy risk | 4 | Drug Interactions | Medium |
| Drug classification | 4 | Drug Interactions | Easy-Medium |
| Adverse event analysis | 4 | Drug Interactions | Easy-Hard |

### Per-Category Results (MCP tools)

| Category | Pass/Total | Avg Latency |
|----------|-----------|-------------|
| Drug interactions | 8/8 (100%) | 93ms |
| Side effects | 6/6 (100%) | 692ms |
| Pathway membership | 6/6 (100%) | 792ms |
| Cross-KG federation | 8/8 (100%) | 2,199ms |
| Drug classification | 4/4 (100%) | 98ms |
| Adverse events | 4/4 (100%) | 1,049ms |
| Polypharmacy risk | 3/4 (75%) | 158ms |
| **Total** | **39/40 (98%)** | **920ms** |

## Quick Start

```bash
# 1. Start Samyama Graph (v0.6.1+)
# Download from https://github.com/samyama-ai/samyama-graph

# 2. Load all 3 KG snapshots
curl -X POST -F "file=@pathways.sgsnap" http://localhost:8080/api/snapshot/import
curl -X POST -F "file=@druginteractions.sgsnap" http://localhost:8080/api/snapshot/import
curl -X POST -F "file=@clinical-trials.sgsnap" http://localhost:8080/api/snapshot/import

# 3. Validate scenarios (no server needed)
pip install requests
python runner.py --dry-run

# 4. Run MCP tools benchmark (Cypher templates)
python runner.py --url http://localhost:8080

# 5. Run baselines (requires OpenAI API key + NLQ-configured tenant)
# See PLAYBOOK.md for tenant setup with schema-aware NLQ config
OPENAI_API_KEY=sk-... python baseline_runner.py --url http://localhost:8080 --tenant biomedqa
```

## Evaluation Approaches

### MCP Tools (98% accuracy)
Pre-authored Cypher templates with parameter substitution. The LLM selects which tool to call and provides arguments. The database executes the template deterministically. Zero schema errors.

### Text-to-Cypher via NLQ (85% accuracy)
Samyama's built-in NLQ endpoint with per-tenant schema-aware system prompt (full schema with edge directions, property types, few-shot examples). GPT-4o generates Cypher server-side. Fails on cross-KG joins (schema hallucination) and exact-vs-CONTAINS matching.

### GPT-4o Standalone (75% accuracy)
GPT-4o answers from training data without database access. Strong on general pharmacology knowledge but fails on precise identifiers (DrugBank IDs), exact counts, and shared-target queries.

## Cross-KG Federation

The benchmark includes 8 cross-KG queries that join across multiple knowledge graphs using WHERE-based property bridges:

```cypher
-- Drug Interactions → Pathways: drug targets → biological pathways
MATCH (d:Drug {name: 'Metformin'})-[:INTERACTS_WITH_GENE]->(g:Gene)
MATCH (p:Protein)-[:PARTICIPATES_IN]->(pw:Pathway)
WHERE p.name = g.gene_name
RETURN g.gene_name, pw.name

-- Drug Interactions → Clinical Trials: drug → clinical trials testing it
MATCH (d:Drug {name: 'Warfarin'})
MATCH (i:Intervention)<-[:TESTS]-(ct:ClinicalTrial)
WHERE i.name = d.name
RETURN ct.nct_id, ct.phase

-- Clinical Trials: breast cancer trial landscape
MATCH (ct:ClinicalTrial)-[:STUDIES]->(c:Condition)
WHERE c.name CONTAINS 'Breast'
RETURN c.name, count(ct) AS trials ORDER BY trials DESC

-- 3-KG chain: diabetes drugs → gene targets → pathways
MATCH (d:Drug)-[:HAS_INDICATION]->(ind:Indication)
WHERE ind.name CONTAINS 'Diabetes'
MATCH (d)-[:INTERACTS_WITH_GENE]->(g:Gene)
WITH DISTINCT g.gene_name AS gene LIMIT 20
MATCH (p:Protein)-[:PARTICIPATES_IN]->(pw:Pathway)
WHERE p.name = gene
RETURN gene, pw.name
```

## Scenario Format

```json
{
  "id": "xkg_002",
  "category": "cross_kg_federation",
  "question": "What biological pathways do Metformin's gene targets participate in?",
  "expected_tools": ["drug_interactions", "protein_pathways"],
  "cypher": "MATCH (d:Drug {name: 'Metformin'})-[:INTERACTS_WITH_GENE]->(g:Gene) MATCH (p:Protein)-[:PARTICIPATES_IN]->(pw:Pathway) WHERE p.name = g.gene_name RETURN g.gene_name, pw.name LIMIT 5",
  "expected_output_contains": [],
  "kgs_required": ["druginteractions", "pathways"],
  "difficulty": "hard"
}
```

## FAQ

### Why does GPT-4o standalone (75%) outperform text-to-Cypher in some categories?

These are fundamentally different tasks. GPT-4o standalone answers from **training data memory** — it knows pharmacology. Text-to-Cypher generates **Cypher queries** that must be syntactically and semantically correct against a 19-label, 12-edge-type schema across 3 KGs. With a schema-aware NLQ endpoint (full schema in system prompt + few-shot examples), text-to-Cypher reaches 85% — but still fails on cross-KG joins where it hallucinates non-existent edge traversals.

### Could text-to-Cypher improve further?

Yes. The Remote Planet demo (single KG, 10 labels) achieved 100% NLQ accuracy with iterative prompt engineering. But the BiomedQA schema is 3x more complex (19 labels, 12 edge types, 3 federated KGs), making text-to-Cypher fundamentally harder. MCP tools eliminate the schema complexity problem entirely.

### Why not test with other LLMs?

GPT-4o is the strongest available baseline. If GPT-4o can't beat MCP tools, weaker models won't either. The conclusion holds across model families.

## Papers

This benchmark is used in:
- **[arXiv:2603.15080](https://arxiv.org/abs/2603.15080)** — Open Biomedical Knowledge Graphs at Scale
- **GRADES-NDA 2026** (SIGMOD workshop) — Federated Biomedical Knowledge Graphs
- **aiDM 2026** (SIGMOD workshop) — Domain-Specific MCP Tools vs. Generic Text-to-Cypher

## Hardware

All results verified on AWS g4dn.4xlarge (16 vCPU AMD EPYC, 62GB RAM, NVIDIA A10G) with all 3 KGs loaded (7.9M nodes, 28M edges). Results reproduced across 4 independent fresh-load runs.

## License

Apache License 2.0
