# Day 9 Vector Query Test

Run the raw and filtered Chroma query with:

```powershell
python day9_vector_query.py
```

Question:

> Is physical therapy covered under the Silver plan?

The script loads Day 7 embeddings, upserts them into the persistent `coverage_kb` collection in batches of 100, verifies `collection.count()`, and runs both:

```python
collection.query(query_embeddings=[...], n_results=5)
collection.query(query_embeddings=[...], n_results=5, where={"plan_type": "Silver"})
```

## Review

The current Day 5 policy files are empty, so the knowledge base currently contains only three plan-summary chunks. The raw query therefore returns the plan summaries, but it cannot verify physical-therapy coverage or retrieve policy exclusions/covered-services clauses. The filtered query is correctly scoped to the Silver plan and returns only the Silver plan record; this confirms metadata filtering, while also documenting the current retrieval miss caused by missing policy text.
