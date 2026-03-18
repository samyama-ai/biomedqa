# BiomedQA Benchmark

40 pharmacology questions over three federated biomedical knowledge graphs, designed to evaluate AI agent approaches for domain-specific data access.

## Key Results

| Approach | Accuracy | Avg Latency | Tokens/Query |
|----------|----------|-------------|-------------|
| GPT-4o standalone | 30/40 (75%) | 2,474ms | 213 |
| Text-to-Cypher (GPT-4o) | 0/40 (0%) | 986ms | 548 |
| **MCP tools** | **39/40 (98%)** | **651ms** | **0** |

**Key finding:** Domain-specific MCP tools (parameterized Cypher templates) outperform both text-to-Cypher and standalone LLM approaches. The LLM's role should be tool selection and argument extraction, not query generation.

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
| Cross-KG federation | 8 | All 3 | Hard |
| Polypharmacy risk | 4 | Drug Interactions | Medium-Hard |
| Drug classification | 4 | Drug Interactions | Easy-Medium |
| Adverse event analysis | 4 | Drug Interactions | Easy-Hard |

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

# 5. Run baselines (requires OpenAI API key)
OPENAI_API_KEY=sk-... python baseline_runner.py --url http://localhost:8080
```

## Evaluation Approaches

### MCP Tools (98% accuracy)
Pre-authored Cypher templates with parameter substitution. The LLM selects which tool to call and provides arguments. The database executes the template deterministically.

### Text-to-Cypher (0% accuracy)
GPT-4o generates Cypher given the schema (3 few-shot examples). Queries are syntactically valid but return empty results due to schema mismatches (hallucinated property filters, incorrect multi-MATCH patterns).

### GPT-4o Standalone (75% accuracy)
GPT-4o answers from training data without database access. Surprisingly effective for general pharmacology knowledge but fails on precise identifiers, exact counts, and shared-target queries.

## Cross-KG Federation

The benchmark includes 8 cross-KG queries that join across multiple knowledge graphs:

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
```

## Scenario Format

```json
{
  "id": "di_001",
  "category": "drug_interactions",
  "question": "What genes does Acetylsalicylic acid interact with?",
  "expected_tools": ["drug_interactions"],
  "expected_tool_args": {"drug_name": "Acetylsalicylic acid"},
  "cypher": "MATCH (d:Drug {name: 'Acetylsalicylic acid'})-[i:INTERACTS_WITH_GENE]->(g:Gene) RETURN g.gene_name, i.interaction_type ORDER BY g.gene_name",
  "expected_output_contains": ["PTGS1"],
  "answer_type": "list",
  "kgs_required": ["druginteractions"],
  "difficulty": "easy"
}
```

## FAQ

### Why does GPT-4o standalone (75%) outperform text-to-Cypher (0%)?

These are fundamentally different tasks. GPT-4o standalone answers from **training data memory** — it knows pharmacology. Text-to-Cypher generates **syntactically valid but semantically wrong** Cypher queries that silently return empty results. See [BiomedQA FAQ](https://samyama-ai.github.io/samyama-graph-book/biomedqa_faq.html) for detailed analysis.

## Papers

This benchmark is used in:
- **arXiv:2603.15080** — Open Biomedical Knowledge Graphs at Scale
- **GRADES-NDA 2026** (SIGMOD workshop) — Federated Biomedical Knowledge Graphs
- **aiDM 2026** (SIGMOD workshop) — Domain-Specific MCP Tools vs. Generic Text-to-Cypher

## License

Apache License 2.0
