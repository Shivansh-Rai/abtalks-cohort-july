# Day 8: Vector Databases

## Setup

Chroma is installed locally with:

```powershell
pip install chromadb
```

Run the local ingestion workflow with:

```powershell
python day8_vector_store.py
```

This creates a persistent Chroma client at `chroma_db/` and a collection named `coverage_kb`. The script rebuilds the collection on each run so the collection always matches `knowledge_base_embeddings.jsonl`.

Pinecone is a separate cloud option. It requires creating a free account and serverless index through the Pinecone dashboard, then installing its client if it is selected:

```powershell
pip install pinecone-client
```

No Pinecone account or API key is stored in this repository.

## Comparison

| Concern | Chroma | Pinecone |
| --- | --- | --- |
| Deployment | Local persistent files; runs on the developer machine | Managed cloud serverless index |
| Free-tier limits | No hosted service quota; limited by local disk and machine resources | Free-tier limits depend on the current Pinecone plan |
| Latency | Very low for local development; depends on local hardware | Network round trip plus managed-service query latency |
| Ease of setup | Install one package and provide a local path; no signup | Requires account, API key, project, and index configuration |
| Enterprise per-member/per-plan access | Enforce filters in the application and isolate collections or metadata namespaces; secure the host and database files | Use namespaces and metadata filters, with tenant authorization enforced before query construction and credentials managed server-side |

## Decision

Chroma is the choice for this program because it is fully free, runs locally, requires no signup or API key, and is simple to reproduce from the checked-in knowledge-base artifacts. Its persistent collection is enough for the current learning workload and keeps experiments fast and private. For a real enterprise deployment with many members, strict tenant isolation, high availability, and managed scaling, Pinecone would be worth reconsidering, but access control would still need to be enforced by the application rather than trusted solely to vector metadata filters.