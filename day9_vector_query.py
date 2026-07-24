"""Run raw and plan-filtered semantic queries against Chroma."""

from __future__ import annotations

from typing import Any

import chromadb

from day8_vector_store import build_collection, load_embedded_chunks
from embeddings import embed


QUESTION = "Is physical therapy covered under the Silver plan?"


def run_query(
	collection: Any,
	query_embedding: list[float],
	where: dict[str, str] | None = None,
	result_count: int = 5,
) -> dict[str, list[list[Any]]]:
	"""Query Chroma with one precomputed question embedding."""
	return collection.query(
		query_embeddings=[query_embedding],
		n_results=result_count,
		where=where,
	)


def _print_results(title: str, result: dict[str, list[list[Any]]]) -> None:
	print(f"\n{title}")
	for rank, (chunk_id, text, metadata) in enumerate(
		zip(result["ids"][0], result["documents"][0], result["metadatas"][0]),
		start=1,
	):
		print(f"{rank}. {chunk_id} [{metadata['plan_type']}] {text}")


def main() -> None:
	chunks = load_embedded_chunks()
	_, collection = build_collection(chunks)

	assert collection.count() == len(chunks)
	question_embedding = embed(QUESTION)
	raw_results = run_query(collection, question_embedding)
	filtered_results = run_query(collection, question_embedding, where={"plan_type": "Silver"})
	_print_results("Raw results", raw_results)
	_print_results("Silver-plan filtered results", filtered_results)
	print(f"\nCollection count: {collection.count()}")
	print(f"Silver result count: {len(filtered_results['ids'][0])}")


if __name__ == "__main__":
	main()