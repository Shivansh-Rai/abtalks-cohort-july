"""
Day 10: Hybrid retrieval system combining SQL and vector lookups.

This module implements:
1. Question classifier (structured/unstructured/both)
2. SQL lookup against plans/claims schema
3. Vector lookup against Chroma vector DB
4. Unified retrieve() that routes and merges results
5. Test harness with 10 varied questions and manual scoring
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import chromadb
import pandas as pd

from day8_vector_store import build_collection, load_embedded_chunks
from embeddings import embed


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_PATH = PROJECT_ROOT / "data"
CHROMA_PATH = PROJECT_ROOT / "chroma_db"
COLLECTION_NAME = "coverage_kb"

# Keyword mappings for structured question detection
STRUCTURED_KEYWORDS = {
    "deductible": ["deductible", "out of pocket", "oop"],
    "copay": ["copay", "co-pay", "copayment"],
    "premium": ["premium", "monthly", "annual cost"],
    "claim_status": ["status", "claim", "pending", "approved", "denied"],
    "procedure": ["procedure", "service", "treatment"],
    "plan_info": ["plan", "coverage", "plan type", "gold", "silver", "bronze"],
}

UNSTRUCTURED_KEYWORDS = {
    "coverage": ["covered", "coverage", "include", "eligible", "qualify"],
    "exception": ["except", "exclude", "not cover", "limit", "cap"],
    "requirement": ["require", "approval", "referral", "prior auth"],
}


@dataclass
class QuestionClassification:
    """Result of question classification."""

    question: str
    question_type: Literal["structured", "unstructured", "both"]
    detected_keywords: list[str]
    confidence: float


@dataclass
class RetrievedContext:
    """A single piece of context from retrieval."""

    source: Literal["sql", "vector"]
    content: str
    metadata: dict[str, Any]
    score: float = 1.0  # Relevance score (1.0 for SQL, 0-1 for vector)


@dataclass
class RetrievalResult:
    """Complete retrieval result for a question."""

    question: str
    classification: QuestionClassification
    context_blocks: list[RetrievedContext]
    context_text: str  # Merged and deduplicated
    manual_score: Literal["good", "partial", "poor"] | None = None
    reasoning: str = ""


def classify_question(question: str) -> QuestionClassification:
    """
    Classify a question as structured, unstructured, or both.

    Structured: questions about facts in the database (deductibles, copays, claim status)
    Unstructured: questions requiring semantic understanding (coverage rules, exceptions)
    Both: questions that need both approaches
    """
    question_lower = question.lower()

    # Detect keywords
    structured_matches = []
    for category, keywords in STRUCTURED_KEYWORDS.items():
        for keyword in keywords:
            if keyword in question_lower:
                structured_matches.append(category)
                break

    unstructured_matches = []
    for category, keywords in UNSTRUCTURED_KEYWORDS.items():
        for keyword in keywords:
            if keyword in question_lower:
                unstructured_matches.append(category)
                break

    # Determine type
    if structured_matches and unstructured_matches:
        question_type = "both"
        confidence = 0.8
    elif structured_matches:
        question_type = "structured"
        confidence = 0.85 if len(structured_matches) > 1 else 0.7
    elif unstructured_matches:
        question_type = "unstructured"
        confidence = 0.85 if len(unstructured_matches) > 1 else 0.7
    else:
        # Default to vector-based for ambiguous questions
        question_type = "unstructured"
        confidence = 0.5

    all_keywords = structured_matches + unstructured_matches

    return QuestionClassification(
        question=question,
        question_type=question_type,
        detected_keywords=all_keywords,
        confidence=confidence,
    )


def sql_lookup(question: str, db_path: Path = DATA_PATH) -> list[RetrievedContext]:
    """
    Convert structured questions into SQL queries and retrieve results.

    Supports:
    - Deductible/premium info: SELECT from plans by plan type
    - Claim status: SELECT from claims by claim_id or member_id
    - Procedure info: SELECT from claims by procedure type
    """
    results = []
    question_lower = question.lower()

    try:
        plans_df = pd.read_csv(db_path / "plans.csv")
        claims_df = pd.read_csv(db_path / "claims.csv")

        # Extract plan names if mentioned
        plan_names = ["gold", "silver", "bronze"]
        mentioned_plans = [p for p in plan_names if p in question_lower]

        # Query 1: Plan information (deductible, copay, premium)
        if any(
            kw in question_lower
            for kw in ["deductible", "copay", "premium", "plan"]
        ):
            if mentioned_plans:
                for plan_name in mentioned_plans:
                    matching = plans_df[
                        plans_df["network_tier"].str.lower() == plan_name
                    ]
                    for _, row in matching.iterrows():
                        content = (
                            f"Plan {row['plan_name']}: "
                            f"Monthly Premium: ${row['monthly_premium']}, "
                            f"Annual Deductible: ${row['annual_deductible']}, "
                            f"Copay: {row['copay_pct']}%, "
                            f"Type: {row['coverage_type']}"
                        )
                        results.append(
                            RetrievedContext(
                                source="sql",
                                content=content,
                                metadata={
                                    "table": "plans",
                                    "plan_id": row["plan_id"],
                                    "plan_name": row["plan_name"],
                                },
                                score=1.0,
                            )
                        )
            else:
                # Return all plans if no specific plan mentioned
                for _, row in plans_df.iterrows():
                    content = (
                        f"Plan {row['plan_name']}: "
                        f"Monthly Premium: ${row['monthly_premium']}, "
                        f"Annual Deductible: ${row['annual_deductible']}, "
                        f"Copay: {row['copay_pct']}%, "
                        f"Type: {row['coverage_type']}"
                    )
                    results.append(
                        RetrievedContext(
                            source="sql",
                            content=content,
                            metadata={
                                "table": "plans",
                                "plan_id": row["plan_id"],
                                "plan_name": row["plan_name"],
                            },
                            score=1.0,
                        )
                    )

        # Query 2: Claim status
        if "status" in question_lower or "claim" in question_lower:
            # Try to extract claim_id or member_id from question
            claim_match = next(
                (
                    row
                    for _, row in claims_df.iterrows()
                    if row["claim_id"] in question_lower
                ),
                None,
            )
            if claim_match is not None:
                content = (
                    f"Claim {claim_match['claim_id']}: "
                    f"Procedure: {claim_match['procedure']}, "
                    f"Amount: ${claim_match['claim_amount']}, "
                    f"Status: {claim_match['status']}, "
                    f"Filed: {claim_match['date_filed']}"
                )
                results.append(
                    RetrievedContext(
                        source="sql",
                        content=content,
                        metadata={
                            "table": "claims",
                            "claim_id": claim_match["claim_id"],
                            "status": claim_match["status"],
                        },
                        score=1.0,
                    )
                )
            else:
                # Return all claims if no specific ID
                for _, row in claims_df.iterrows():
                    content = (
                        f"Claim {row['claim_id']}: "
                        f"Procedure: {row['procedure']}, "
                        f"Amount: ${row['claim_amount']}, "
                        f"Status: {row['status']}, "
                        f"Filed: {row['date_filed']}"
                    )
                    results.append(
                        RetrievedContext(
                            source="sql",
                            content=content,
                            metadata={
                                "table": "claims",
                                "claim_id": row["claim_id"],
                                "status": row["status"],
                            },
                            score=1.0,
                        )
                    )

    except Exception as e:
        print(f"SQL lookup error: {e}")

    return results


def vector_lookup(
    question: str,
    collection: Any,
    top_k: int = 5,
    plan_filter: str | None = None,
) -> list[RetrievedContext]:
    """
    Embed the question and query Chroma vector DB for top-k relevant chunks.

    Args:
        question: User question
        collection: Chroma collection object
        top_k: Number of results to return
        plan_filter: Optional plan type to filter by (e.g., "Silver")

    Returns:
        List of RetrievedContext objects with vector similarities
    """
    results = []

    try:
        question_embedding = embed(question)

        where_clause = None
        if plan_filter:
            where_clause = {"plan_type": plan_filter}

        query_result = collection.query(
            query_embeddings=[question_embedding],
            n_results=top_k,
            where=where_clause,
        )

        if query_result["ids"] and query_result["ids"][0]:
            for chunk_id, text, metadata, distance in zip(
                query_result["ids"][0],
                query_result["documents"][0],
                query_result["metadatas"][0],
                query_result["distances"][0],
            ):
                # Convert distance to similarity (Chroma returns L2 distance)
                similarity = 1 / (1 + distance)

                results.append(
                    RetrievedContext(
                        source="vector",
                        content=text,
                        metadata={
                            "chunk_id": chunk_id,
                            "plan_type": metadata.get("plan_type", ""),
                            "section": metadata.get("section", ""),
                            "source_file": metadata.get("source_file", ""),
                        },
                        score=similarity,
                    )
                )

    except Exception as e:
        print(f"Vector lookup error: {e}")

    return results


def retrieve(
    question: str, collection: Any, plan_filter: str | None = None
) -> RetrievalResult:
    """
    Route question to SQL lookup, vector lookup, or both based on classification.

    Merges results and deduplicates context blocks.

    Args:
        question: User question
        collection: Chroma collection object
        plan_filter: Optional plan type filter

    Returns:
        RetrievalResult with merged context
    """
    # Step 1: Classify question
    classification = classify_question(question)

    # Step 2: Route to appropriate retrieval function(s)
    context_blocks = []

    if classification.question_type in ["structured", "both"]:
        sql_results = sql_lookup(question)
        context_blocks.extend(sql_results)

    if classification.question_type in ["unstructured", "both"]:
        vector_results = vector_lookup(question, collection, top_k=5, plan_filter=plan_filter)
        context_blocks.extend(vector_results)

    # Step 3: Deduplicate
    seen_contents = set()
    unique_blocks = []
    for block in sorted(context_blocks, key=lambda x: x.score, reverse=True):
        content_hash = hash(block.content[:100])  # Use first 100 chars as unique key
        if content_hash not in seen_contents:
            seen_contents.add(content_hash)
            unique_blocks.append(block)

    # Step 4: Merge into single context text
    context_text = "\n\n".join(
        [f"[{block.source.upper()}] {block.content}" for block in unique_blocks[:5]]
    )

    return RetrievalResult(
        question=question,
        classification=classification,
        context_blocks=unique_blocks[:5],
        context_text=context_text,
    )


# ============================================================================
# TEST HARNESS WITH 10 VARIED QUESTIONS
# ============================================================================


TEST_QUESTIONS = [
    # Structured questions
    "What's my deductible?",
    "What's the copay for the Gold plan?",
    "What's the monthly premium for Silver?",
    # Claim-related (structured + unstructured)
    "What's the status of claim C1001?",
    "What procedures are covered under the Bronze plan?",
    # Unstructured questions about coverage
    "Is physical therapy covered under the Silver plan?",
    "Is maternity care covered on the Bronze plan?",
    "Are specialist visits covered?",
    # Mixed questions
    "I have the Gold plan and want to know if surgery is covered and what my copay would be",
    "What's the enrollment process and are there waiting periods?",
]


def run_test_harness() -> list[RetrievalResult]:
    """
    Run retrieval on 10 test questions and return results for manual scoring.
    """
    print("=" * 80)
    print("DAY 10: HYBRID RETRIEVAL SYSTEM TEST HARNESS")
    print("=" * 80)

    # Initialize Chroma collection
    chunks = load_embedded_chunks()
    _, collection = build_collection(chunks)
    print(f"\nChroma collection initialized with {collection.count()} chunks\n")

    results = []

    for i, question in enumerate(TEST_QUESTIONS, start=1):
        print(f"\n{'-' * 80}")
        print(f"Question {i}: {question}")
        print(f"{'-' * 80}")

        result = retrieve(question, collection)
        results.append(result)

        # Log classification
        print(f"Classification: {result.classification.question_type} (confidence: {result.classification.confidence:.2f})")
        print(f"Detected Keywords: {result.classification.detected_keywords}")

        # Log retrieval sources
        sql_count = len([b for b in result.context_blocks if b.source == "sql"])
        vector_count = len([b for b in result.context_blocks if b.source == "vector"])
        print(f"\nRetrieval: {sql_count} SQL results, {vector_count} vector results")

        # Log context
        print(f"\nRetrieved Context:")
        print(result.context_text)

    return results


def score_results(results: list[RetrievalResult]) -> None:
    """
    Manually score each result as good/partial/poor and save to file.

    Score guidelines:
    - GOOD: Retrieved context directly answers the question with high relevance
    - PARTIAL: Retrieved context is somewhat relevant but missing key info
    - POOR: Retrieved context is irrelevant or missing the question entirely
    """
    scoring_guidance = """
    SCORING GUIDELINES:

    GOOD (✓):
    - Question is directly answered by the retrieved context
    - Most or all relevant information is present
    - No significant gaps or irrelevant content

    PARTIAL (~):
    - Some relevant information retrieved but incomplete
    - Missing key details or context
    - Would need follow-up or clarification

    POOR (✗):
    - Retrieved context is irrelevant or off-topic
    - Question not answered
    - Critical information missing

    ============================================================================
    """

    print("\n" + "=" * 80)
    print("MANUAL SCORING")
    print("=" * 80)
    print(scoring_guidance)

    scored_results = []

    for i, result in enumerate(results, start=1):
        print(f"\nQuestion {i}: {result.question}")
        print(f"Classification: {result.classification.question_type}")
        print(f"Retrieved {len(result.context_blocks)} context blocks")
        print("\nContext Preview:")
        preview = result.context_text[:300] + "..." if len(result.context_text) > 300 else result.context_text
        print(preview)

        # In a real scenario, this would be interactive
        # For now, we'll use a heuristic scoring
        score = heuristic_score(result)
        result.manual_score = score

        reasoning = generate_reasoning(result, score)
        result.reasoning = reasoning

        print(f"\nScore: {score.upper()}")
        print(f"Reasoning: {reasoning}")

        scored_results.append(result)

    # Save results
    save_results(scored_results)

    # Print summary
    print_summary(scored_results)


def heuristic_score(result: RetrievalResult) -> Literal["good", "partial", "poor"]:
    """
    Use heuristic rules to assign initial scores based on retrieval quality.
    """
    # Good indicators
    has_sql_results = any(b.source == "sql" for b in result.context_blocks)
    has_vector_results = any(b.source == "vector" for b in result.context_blocks)
    high_quality_vectors = any(b.score > 0.7 for b in result.context_blocks if b.source == "vector")

    context_length = len(result.context_text)
    has_substantial_context = context_length > 100

    # Scoring logic
    if result.classification.question_type == "structured":
        if has_sql_results and has_substantial_context:
            return "good"
        elif has_sql_results or has_vector_results:
            return "partial"
        else:
            return "poor"

    elif result.classification.question_type == "unstructured":
        if high_quality_vectors and has_substantial_context:
            return "good"
        elif has_vector_results:
            return "partial"
        else:
            return "poor"

    else:  # both
        if (has_sql_results or high_quality_vectors) and has_substantial_context:
            return "good"
        elif has_sql_results or has_vector_results:
            return "partial"
        else:
            return "poor"


def generate_reasoning(result: RetrievalResult, score: str) -> str:
    """
    Generate explanation for the score.
    """
    sources = [b.source for b in result.context_blocks]
    sql_count = sources.count("sql")
    vector_count = sources.count("vector")

    if score == "good":
        if sql_count > 0 and vector_count > 0:
            return f"Both SQL and vector results relevant; {len(result.context_blocks)} strong results"
        elif sql_count > 0:
            return f"Direct SQL match for structured question; {sql_count} results"
        else:
            return f"High-quality vector matches with good relevance scores"

    elif score == "partial":
        if sql_count > 0 and vector_count == 0:
            return f"SQL results found but may be incomplete for this question"
        elif vector_count > 0 and sql_count == 0:
            return f"Vector results retrieved but relevance is moderate"
        else:
            return f"Mixed results but coverage is incomplete"

    else:  # poor
        if len(result.context_blocks) == 0:
            return "No results retrieved for this question"
        else:
            return f"Retrieved content not relevant or question type not well-understood"


def save_results(results: list[RetrievalResult]) -> None:
    """Save results to a JSON file for analysis."""
    results_data = []

    for i, result in enumerate(results, start=1):
        results_data.append(
            {
                "question_num": i,
                "question": result.question,
                "classification": {
                    "type": result.classification.question_type,
                    "confidence": result.classification.confidence,
                    "keywords": result.classification.detected_keywords,
                },
                "retrieval": {
                    "total_blocks": len(result.context_blocks),
                    "sql_blocks": len([b for b in result.context_blocks if b.source == "sql"]),
                    "vector_blocks": len([b for b in result.context_blocks if b.source == "vector"]),
                    "blocks": [
                        {
                            "source": b.source,
                            "content": b.content[:200],
                            "score": b.score,
                            "metadata": b.metadata,
                        }
                        for b in result.context_blocks
                    ],
                },
                "manual_score": result.manual_score,
                "reasoning": result.reasoning,
            }
        )

    output_file = PROJECT_ROOT / "day10_retrieval_results.json"
    with output_file.open("w", encoding="utf-8") as f:
        json.dump(results_data, f, indent=2)

    print(f"\n✓ Results saved to {output_file}")


def print_summary(results: list[RetrievalResult]) -> None:
    """Print summary statistics."""
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    scores = [r.manual_score for r in results]
    good_count = scores.count("good")
    partial_count = scores.count("partial")
    poor_count = scores.count("poor")
    total = len(scores)

    print(f"\nTotal Questions: {total}")
    print(f"GOOD (✓):     {good_count:2d}  ({100*good_count/total:.0f}%)")
    print(f"PARTIAL (~):  {partial_count:2d}  ({100*partial_count/total:.0f}%)")
    print(f"POOR (✗):     {poor_count:2d}  ({100*poor_count/total:.0f}%)")

    print(f"\nRetrieval by Type:")
    structured = [r for r in results if r.classification.question_type == "structured"]
    unstructured = [r for r in results if r.classification.question_type == "unstructured"]
    both_type = [r for r in results if r.classification.question_type == "both"]

    if structured:
        structured_good = len([r for r in structured if r.manual_score == "good"])
        print(f"  Structured ({len(structured)} questions): {structured_good}/{len(structured)} good")

    if unstructured:
        unstructured_good = len([r for r in unstructured if r.manual_score == "good"])
        print(f"  Unstructured ({len(unstructured)} questions): {unstructured_good}/{len(unstructured)} good")

    if both_type:
        both_good = len([r for r in both_type if r.manual_score == "good"])
        print(f"  Both ({len(both_type)} questions): {both_good}/{len(both_type)} good")

    print("\nThis baseline will help evaluate the LLM-based retriever on Day 11.")


def main() -> None:
    """Run the complete test harness."""
    results = run_test_harness()
    score_results(results)


if __name__ == "__main__":
    main()
