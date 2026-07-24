"""Build a persistent local Chroma collection from Day 7 embeddings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import chromadb


PROJECT_ROOT = Path(__file__).resolve().parent
EMBEDDED_KNOWLEDGE_BASE_PATH = PROJECT_ROOT / "knowledge_base_embeddings.jsonl"
CHROMA_PATH = PROJECT_ROOT / "chroma_db"
COLLECTION_NAME = "coverage_kb"
BATCH_SIZE = 100


def load_embedded_chunks(
	path: Path = EMBEDDED_KNOWLEDGE_BASE_PATH,
) -> list[dict[str, Any]]:
	"""Load embedded knowledge-base records from JSONL."""
	with path.open("r", encoding="utf-8") as embedded_file:
		return [json.loads(line) for line in embedded_file if line.strip()]


def _chroma_metadata(
	chunk: dict[str, Any],
	plans_by_id: dict[str, dict[str, Any]],
) -> dict[str, str | int]:
	"""Convert record metadata to Chroma's scalar metadata format."""
	plan = plans_by_id.get(chunk["id"].removeprefix("plan:"), {})
	return {
		"source_file": chunk["source_file"],
		"source_type": chunk["source_type"],
		"plan_type": plan.get("network_tier", chunk["plan_type"] or ""),
		"coverage_type": plan.get("coverage_type", ""),
		"section": chunk["section"],
		"ingested_at": chunk["ingested_at"],
	}


def build_collection(
	chunks: list[dict[str, Any]],
	storage_path: Path = CHROMA_PATH,
):
	"""Create or rebuild the persistent coverage knowledge collection."""
	client = chromadb.PersistentClient(path=str(storage_path))
	try:
		client.delete_collection(COLLECTION_NAME)
	except Exception:
		pass
	collection = client.create_collection(COLLECTION_NAME)
	plans_path = PROJECT_ROOT / "data" / "plans.json"
	plans = json.loads(plans_path.read_text(encoding="utf-8"))
	plans_by_id = {plan["plan_id"]: plan for plan in plans}

	for start in range(0, len(chunks), BATCH_SIZE):
		batch = chunks[start : start + BATCH_SIZE]
		collection.add(
			ids=[chunk["id"] for chunk in batch],
			documents=[chunk["text"] for chunk in batch],
			embeddings=[chunk["embedding"] for chunk in batch],
			metadatas=[_chroma_metadata(chunk, plans_by_id) for chunk in batch],
		)
	return client, collection


def main() -> None:
	chunks = load_embedded_chunks()
	_, collection = build_collection(chunks)
	print(f"Stored {collection.count()} chunks in '{COLLECTION_NAME}'")
	print(f"Persistent storage: {CHROMA_PATH}")


if __name__ == "__main__":
	main()