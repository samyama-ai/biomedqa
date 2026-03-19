# BiomedQA Benchmark Playbook

**Version**: v0.6.1
**Data**: 7.9M nodes, 28M edges — 3 biomedical KGs from 13 open data sources
**Hardware**: AWS g4dn.4xlarge (16 vCPU, 62GB RAM, NVIDIA A10G)
**Verified**: 2026-03-17 (4 independent runs, 39/40 on each)

---

## 0. Prerequisites

### 0.1 Build Enterprise Binary (from local machine)

```bash
cd /Users/user/projects/Madhulatha-Sandeep/graph_ws/samyama-graph-enterprise
rsync -avz --exclude='target' --exclude='samyama_data' --exclude='.git' --exclude='data' \
  -e ssh . ubuntu@aws-gpu-vm:~/samyama-graph-enterprise/

ssh ubuntu@aws-gpu-vm
source $HOME/.cargo/env
cd ~/samyama-graph-enterprise

SAMYAMA_EMBEDDED_PUBLIC_KEY=$(cat config/public.key) cargo build --release --features cuda
cp target/release/samyama ~/samyama/bin/samyama-enterprise
```

### 0.2 Start Server

```bash
ssh ubuntu@aws-gpu-vm

# Clean previous data
pkill -f samyama 2>/dev/null
rm -rf ~/samyama/samyama_data

# Start
cd ~/samyama && nohup ./bin/samyama-enterprise > logs/server.log 2>&1 &
sleep 3
curl -s http://localhost:8080/api/status
# Expected: {"status":"healthy","storage":{"edges":0,"nodes":0},"version":"0.6.1"}
```

### 0.3 Load All 3 KGs

```bash
# Pathways (~3.5s)
time curl -s -X POST -F "file=@/home/ubuntu/samyama/data/pathways.sgsnap" \
  http://localhost:8080/api/snapshot/import
# Expected: 118,686 nodes, 834,785 edges

# Drug Interactions (~0.7s)
time curl -s -X POST -F "file=@/home/ubuntu/samyama/data/druginteractions.sgsnap" \
  http://localhost:8080/api/snapshot/import
# Expected: 32,726 nodes, 191,970 edges

# Clinical Trials (~3 min)
time curl -s -X POST -F "file=@/home/ubuntu/samyama/data/clinical-trials.sgsnap" \
  http://localhost:8080/api/snapshot/import
# Expected: 7,774,446 nodes, 26,973,997 edges

# Verify total
curl -s http://localhost:8080/api/status
# Expected: {"storage":{"edges":28000752,"nodes":7925858}}
```

### 0.4 Sync Benchmark Files to VM

```bash
# From local machine
scp -r scenarios runner.py baseline_runner.py ubuntu@aws-gpu-vm:~/samyama/biomedqa/
```

### 0.5 Install Python Dependencies on VM

```bash
ssh ubuntu@aws-gpu-vm
source ~/venv/bin/activate
pip install requests openai -q
```

---

## 1. Run MCP Tools Benchmark (98% expected)

```bash
ssh ubuntu@aws-gpu-vm
source ~/venv/bin/activate
cd ~/samyama/biomedqa

python3 runner.py --url http://localhost:8080 --output ~/samyama/results_mcp.json
```

Expected output:
```
Total:    40
Passed:   39 (98%)
Failed:   1
Avg lat:  920 ms

Category                    Pass  Total    Pct   Avg ms
───────────────────────────────────────────────────────
  adverse_events              4     4   100%  1049
  cross_kg_federation         8     8   100%  2199
  drug_classification         4     4   100%    98
  drug_interactions           8     8   100%    93
  pathway_membership          6     6   100%   792
  polypharmacy_risk           3     4    75%   158
  side_effects                6     6   100%   692
```

The single failure (pr_004) is a correct empty result — the queried drugs genuinely share no gene targets.

---

## 2. Run Text-to-Cypher Baseline (85% expected)

Requires a tenant with NLQ configured (see Step 0.3 in setup_tenant.sh):

```bash
OPENAI_API_KEY='sk-proj-...' \
python3 baseline_runner.py --url http://localhost:8080 --tenant biomedqa --mode text-to-cypher \
  --output ~/samyama/results_t2c.json
```

Uses Samyama's NLQ endpoint with schema-aware system prompt. 6 failures: 3 schema hallucinations, 1 exact-vs-CONTAINS, 1 inline property variable, 1 correct empty.

---

## 3. Run GPT-4o Standalone Baseline (75% expected)

```bash
OPENAI_API_KEY='sk-proj-...' \
python3 baseline_runner.py --url http://localhost:8080 --mode standalone \
  --output ~/samyama/results_standalone.json
```

GPT-4o answers from training data — no database access.

---

## 4. Run All Three Together

```bash
# MCP tools
python3 runner.py --url http://localhost:8080 --output ~/samyama/results_mcp.json

# Both baselines
OPENAI_API_KEY='sk-proj-...' \
python3 baseline_runner.py --url http://localhost:8080 --mode both \
  --output ~/samyama/results_baselines.json
```

---

## 5. Verify Cross-KG Queries

The benchmark includes 8 cross-KG queries. Verify they actually cross KG boundaries:

```bash
# Drug Interactions → Pathways (gene targets → biological pathways)
redis-cli -p 6379 GRAPH.QUERY default "MATCH (d:Drug {name: 'Metformin'})-[:INTERACTS_WITH_GENE]->(g:Gene) MATCH (p:Protein)-[:PARTICIPATES_IN]->(pw:Pathway) WHERE p.name = g.gene_name RETURN g.gene_name, pw.name LIMIT 3"

# Drug Interactions → Clinical Trials (drug → trials testing it)
redis-cli -p 6379 GRAPH.QUERY default "MATCH (d:Drug {name: 'Warfarin'}) MATCH (i:Intervention)<-[:TESTS]-(ct:ClinicalTrial) WHERE i.name = d.name RETURN ct.nct_id, ct.phase LIMIT 3"

# Clinical Trials only (breast cancer landscape)
redis-cli -p 6379 GRAPH.QUERY default "MATCH (ct:ClinicalTrial)-[:STUDIES]->(c:Condition) WHERE c.name CONTAINS 'Breast' RETURN c.name, count(ct) AS trials ORDER BY trials DESC LIMIT 3"
```

---

## 6. Resource Monitoring

```bash
# Check memory and status during benchmark
bash ~/samyama/monitor.sh
```

Expected: ~33GB RSS for 7.9M nodes (53% of 62GB available).

---

## 7. Teardown

```bash
ssh ubuntu@aws-gpu-vm 'pkill -f samyama || true'
# (Optional) Shutdown VM to save costs
ssh ubuntu@aws-gpu-vm 'sudo shutdown now'
```

---

## Appendix: Verified Numbers

All numbers verified across 4 independent fresh-load runs:

| Metric | Value | Verified |
|--------|-------|----------|
| Pathways nodes | 118,686 | Exact across 4 runs |
| Drug Interactions nodes | 32,726 | Exact |
| Clinical Trials nodes | 7,774,446 | Exact |
| Combined nodes | 7,925,858 | Exact |
| Combined edges | 28,000,752 | Exact |
| MCP accuracy | 39/40 (98%) | 4 runs |
| MCP avg latency | 920ms | Variance |
| Text-to-Cypher (NLQ) accuracy | 34/40 (85%) | 1 run (schema-aware) |
| GPT-4o standalone accuracy | 30/40 (75%) | 1 run |
| Pathways load time | 3.4-3.6s | Variance |
| Drug Interactions load time | 0.7-0.8s | Variance |
| Clinical Trials load time | 174-181s | Variance |
| Memory usage | 32.6-33.7GB | Variance |
