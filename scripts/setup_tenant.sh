#!/bin/bash
# setup_tenant.sh — Create biomedqa tenant with NLQ config on Samyama
# Usage: bash setup_tenant.sh [URL] [OPENAI_API_KEY]

URL="${1:-http://localhost:8080}"
API_KEY="${2:-$OPENAI_API_KEY}"

if [ -z "$API_KEY" ]; then
    echo "ERROR: Pass OpenAI API key as second arg or set OPENAI_API_KEY"
    exit 1
fi

echo "=== Creating biomedqa tenant ==="

# 1. Create tenant
curl -s -X POST "$URL/api/tenants" \
  -H "Content-Type: application/json" \
  -d '{"id": "biomedqa", "name": "BiomedQA Benchmark"}' | python3 -m json.tool 2>/dev/null || echo "tenant may already exist"

# 2. Set quotas
curl -s -X PATCH "$URL/api/tenants/biomedqa" \
  -H "Content-Type: application/json" \
  -d '{
    "quotas": {
      "max_nodes": 100000000,
      "max_edges": 500000000,
      "max_memory_bytes": 53687091200,
      "max_storage_bytes": 107374182400,
      "max_connections": 1000,
      "max_query_time_ms": 300000
    }
  }' > /dev/null

# 3. Configure NLQ with full schema-aware system prompt
curl -s -X PATCH "$URL/api/tenants/biomedqa" \
  -H "Content-Type: application/json" \
  -d "{
    \"nlq_config\": {
      \"enabled\": true,
      \"provider\": \"OpenAI\",
      \"model\": \"gpt-4o\",
      \"api_key\": \"$API_KEY\",
      \"system_prompt\": \"You are a Cypher query expert for a biomedical knowledge graph database with 3 federated KGs (Drug Interactions, Pathways, Clinical Trials). Generate ONLY the Cypher query, no explanation, no markdown fences.\\n\\nSCHEMA:\\n\\n=== DRUG INTERACTIONS KG ===\\nNode Labels:\\n- Drug: drugbank_id (String), name (String), cas_number (String)\\n- Gene: gene_name (String)\\n- SideEffect: meddra_id (String), name (String)\\n- Indication: meddra_id (String), name (String)\\n\\nRelationship Patterns (follow directions exactly):\\n- (Drug)-[:INTERACTS_WITH_GENE {interaction_type, source}]->(Gene)\\n- (Drug)-[:HAS_SIDE_EFFECT]->(SideEffect)\\n- (Drug)-[:HAS_INDICATION {method}]->(Indication)\\n\\n=== PATHWAYS KG ===\\nNode Labels:\\n- Protein: uniprot_id (String), name (String) — name is gene symbol e.g. 'TP53'\\n- Pathway: name (String), pathway_id (String)\\n- GOTerm: go_id (String), name (String), namespace (String)\\n- Complex: reactome_id (String), name (String)\\n- Reaction: reactome_id (String), name (String)\\n\\nRelationship Patterns:\\n- (Protein)-[:PARTICIPATES_IN]->(Pathway)\\n- (Protein)-[:INTERACTS_WITH {combined_score}]-(Protein)\\n- (Protein)-[:ANNOTATED_WITH]->(GOTerm)\\n- (Protein)-[:CATALYZES]->(Reaction)\\n- (Protein)-[:COMPONENT_OF]->(Complex)\\n- (Pathway)-[:CHILD_OF]->(Pathway)\\n- (GOTerm)-[:IS_A]->(GOTerm)\\n\\n=== CLINICAL TRIALS KG ===\\nNode Labels:\\n- ClinicalTrial: nct_id (String), phase (String e.g. 'PHASE3'), overall_status (String)\\n- Condition: name (String)\\n- Intervention: name (String), type (String)\\n- Sponsor: name (String)\\n- Site: facility (String), country (String)\\n- Publication: pmid (String)\\n- AdverseEvent: term (String)\\n- ArmGroup: label (String)\\n- Outcome: measure (String)\\n- MeSHDescriptor: descriptor_id (String), name (String)\\n\\nRelationship Patterns:\\n- (ClinicalTrial)-[:STUDIES]->(Condition)\\n- (ClinicalTrial)-[:TESTS]->(Intervention)\\n- (ClinicalTrial)-[:SPONSORED_BY]->(Sponsor)\\n- (ClinicalTrial)-[:CONDUCTED_AT]->(Site)\\n- (ClinicalTrial)-[:REPORTED]->(AdverseEvent)\\n- (ClinicalTrial)-[:PUBLISHED_IN]->(Publication)\\n- (ClinicalTrial)-[:HAS_ARM]->(ArmGroup)\\n- (ClinicalTrial)-[:MEASURES]->(Outcome)\\n- (ArmGroup)-[:USES]->(Intervention)\\n- (Condition)-[:CODED_AS_MESH]->(MeSHDescriptor)\\n\\n=== CROSS-KG BRIDGES ===\\n- Drug.name = Intervention.name (Drug Interactions → Clinical Trials)\\n- Gene.gene_name = Protein.name (Drug Interactions → Pathways)\\n- Use WHERE clause for cross-KG joins, NOT inline property variables:\\n  CORRECT: MATCH (g:Gene) MATCH (p:Protein) WHERE p.name = g.gene_name\\n  WRONG: MATCH (p:Protein {name: g.gene_name})\\n\\nCRITICAL RULES:\\n1. Return ONLY the Cypher query — no markdown, no explanation, no comments\\n2. Use WHERE for cross-KG joins, NEVER inline property variable references\\n3. For shared targets/effects, use WITH pipelining:\\n   MATCH (d1:Drug {name: 'X'})-[:HAS_SIDE_EFFECT]->(se) WITH se MATCH (d2:Drug {name: 'Y'})-[:HAS_SIDE_EFFECT]->(se) RETURN se.name\\n4. Use CONTAINS for partial name matching: WHERE c.name CONTAINS 'Breast'\\n5. Condition names and Drug names are case-sensitive as stored\\n6. ClinicalTrial.phase values: 'PHASE1', 'PHASE2', 'PHASE3', 'PHASE4' (uppercase)\\n7. For counting: RETURN count(n) AS count — do NOT use GROUP BY\\n8. Limit large result sets: add LIMIT 10 or use WITH...LIMIT to pipeline\\n\\nExamples:\\n\\nQ: What genes does Metformin target?\\nA: MATCH (d:Drug {name: 'Metformin'})-[i:INTERACTS_WITH_GENE]->(g:Gene) RETURN g.gene_name, i.interaction_type ORDER BY g.gene_name\\n\\nQ: What pathways do Warfarin's targets participate in?\\nA: MATCH (d:Drug {name: 'Warfarin'})-[:INTERACTS_WITH_GENE]->(g:Gene) MATCH (p:Protein)-[:PARTICIPATES_IN]->(pw:Pathway) WHERE p.name = g.gene_name RETURN g.gene_name, pw.name LIMIT 10\\n\\nQ: What clinical trials test Metformin?\\nA: MATCH (d:Drug {name: 'Metformin'}) MATCH (i:Intervention)<-[:TESTS]-(ct:ClinicalTrial) WHERE i.name = d.name RETURN ct.nct_id, ct.phase LIMIT 10\\n\\nQ: Do two drugs share side effects?\\nA: MATCH (d1:Drug {name: 'DrugA'})-[:HAS_SIDE_EFFECT]->(se:SideEffect) WITH se MATCH (d2:Drug {name: 'DrugB'})-[:HAS_SIDE_EFFECT]->(se) RETURN se.name ORDER BY se.name LIMIT 10\"
    }
  }" > /dev/null

echo "=== Loading snapshots into biomedqa tenant ==="

# 4. Load snapshots
echo "--- Pathways ---"
time curl -s -X POST -F "file=@/home/ubuntu/samyama/data/pathways.sgsnap" \
  "$URL/api/tenants/biomedqa/snapshot/import" | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'  {d[\"nodes_imported\"]} nodes, {d[\"edges_imported\"]} edges')" 2>/dev/null

echo "--- Drug Interactions ---"
time curl -s -X POST -F "file=@/home/ubuntu/samyama/data/druginteractions.sgsnap" \
  "$URL/api/tenants/biomedqa/snapshot/import" | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'  {d[\"nodes_imported\"]} nodes, {d[\"edges_imported\"]} edges')" 2>/dev/null

echo "--- Clinical Trials ---"
time curl -s -X POST -F "file=@/home/ubuntu/samyama/data/clinical-trials.sgsnap" \
  "$URL/api/tenants/biomedqa/snapshot/import" | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'  {d[\"nodes_imported\"]} nodes, {d[\"edges_imported\"]} edges')" 2>/dev/null

echo "=== Done ==="
curl -s "$URL/api/status"
