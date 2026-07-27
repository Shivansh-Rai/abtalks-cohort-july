"""
Day 11: LLM answer generation on top of Day 10 hybrid retrieval.

Free path used: (b) Groq OpenAI-compatible API.
Alternative (a) Ollama is left as a commented placeholder — do not install Ollama here.

Chain: answer = generate_answer(question, retrieve(question))
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from day8_vector_store import build_collection, load_embedded_chunks
from retrieval_engine import TEST_QUESTIONS, RetrievalResult, retrieve


PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# LLM client — path (b) Groq (key from .env; placeholder until you set a real one)
# ---------------------------------------------------------------------------
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "your_groq_api_key_here")
GROQ_MODEL = "llama-3.1-8b-instant"

client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=GROQ_API_KEY,
)

# ---------------------------------------------------------------------------
# Path (a) Ollama — PLACEHOLDER only (do not install Ollama for this exercise)
# Uncomment and use instead of the Groq client above once Ollama is available:
#
#   # ollama.com → install → `ollama pull llama3.1`
#   client = OpenAI(
#       base_url="http://localhost:11434/v1",
#       api_key="ollama",
#   )
#   GROQ_MODEL = "llama3.1"
# ---------------------------------------------------------------------------

GROUNDING_PROMPT = (
    "Answer using ONLY the context below. If the answer isn't in the context, "
    "say you don't know and suggest the member contact support. "
    "This is not medical advice.\n\n"
    "Context: {context}\n\n"
    "Question: {question}"
)

PLACEHOLDER_KEYS = {"", "your_groq_api_key_here", "ollama", "placeholder"}


def _is_placeholder_key() -> bool:
    return GROQ_API_KEY.strip().lower() in PLACEHOLDER_KEYS


def _context_to_text(context: str | RetrievalResult) -> str:
    if isinstance(context, RetrievalResult):
        return context.context_text
    return context


def _fallback_grounded_answer(question: str, context: str) -> str:
    """
    Local grounded reply when the LLM endpoint is unavailable (placeholder key).
    Follows the same rules as the grounding prompt: only use context; otherwise
    say you don't know and suggest support. Not medical advice.
    """
    disclaimer = " This is not medical advice."
    cleaned = (context or "").strip()
    if not cleaned:
        return (
            "I don't know based on the available context. "
            "Please contact support for help with this question."
            + disclaimer
        )

    q = question.lower()
    ctx = cleaned.lower()
    # Topics that must be explicitly present in context to affirm coverage/process.
    coverage_topics = (
        "physical therapy",
        "maternity",
        "specialist",
        "surgery",
        "enrollment",
        "waiting period",
        "procedure",
    )
    missing_topics = [t for t in coverage_topics if t in q and t not in ctx]

    # Structured facts that may still be answerable from plan/claim rows.
    known_bits: list[str] = []
    if "deductible" in q and "deductible" in ctx:
        known_bits.append("The context lists annual deductible amounts for the plans shown.")
    if "copay" in q and "copay" in ctx:
        known_bits.append("The context includes copay figures for the listed plan(s).")
    if "premium" in q and "premium" in ctx:
        known_bits.append("The context includes monthly premium figures for the listed plan(s).")
    if "claim" in q and "claim" in ctx:
        known_bits.append("The context includes claim status rows matching the query.")

    if missing_topics and known_bits:
        topics = ", ".join(missing_topics)
        return (
            f"From the context I can share this: {' '.join(known_bits)} "
            f"Details: {cleaned} "
            f"However, {topics} is not clearly confirmed in the context, so I don't know "
            f"that part. Please contact support for an authoritative answer."
            + disclaimer
        )

    if missing_topics:
        return (
            "I don't know from the context provided - it does not clearly confirm "
            "that coverage or process. Please contact support for an authoritative answer."
            + disclaimer
        )

    return (
        f"Based only on the retrieved context: {cleaned} "
        f"That is what is available for: {question}"
        + disclaimer
    )


def generate_answer(question: str, context: str | RetrievalResult) -> str:
    """
    Generate a grounded natural-language answer from retrieved context.
    """
    context_text = _context_to_text(context)
    prompt = GROUNDING_PROMPT.format(context=context_text, question=question)

    if _is_placeholder_key():
        return _fallback_grounded_answer(question, context_text)

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 — demo-friendly when key/endpoint fails
        print(f"[llm] API call failed ({exc}); using grounded fallback.")
        return _fallback_grounded_answer(question, context_text)


def generate_answer_stream(question: str, context: str | RetrievalResult) -> str:
    """
    Streaming variant using the OpenAI SDK stream=True mode.
    Prints tokens as they arrive; returns the full answer.
    """
    context_text = _context_to_text(context)
    prompt = GROUNDING_PROMPT.format(context=context_text, question=question)

    if _is_placeholder_key():
        answer = _fallback_grounded_answer(question, context_text)
        print("[stream] Placeholder key - simulating incremental tokens:\n")
        for word in answer.split(" "):
            chunk = word + " "
            print(chunk, end="", flush=True)
            time.sleep(0.03)
        print("\n")
        return answer.strip()

    try:
        stream = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            stream=True,
        )
        parts: list[str] = []
        print("[stream] Tokens:\n")
        for event in stream:
            delta = event.choices[0].delta.content or ""
            if delta:
                parts.append(delta)
                print(delta, end="", flush=True)
        print("\n")
        return "".join(parts).strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[stream] API stream failed ({exc}); simulating tokens from fallback.\n")
        answer = _fallback_grounded_answer(question, context_text)
        for word in answer.split(" "):
            print(word + " ", end="", flush=True)
            time.sleep(0.03)
        print("\n")
        return answer.strip()


def _get_collection() -> Any:
    chunks = load_embedded_chunks()
    _, collection = build_collection(chunks)
    return collection


def answer_pipeline(question: str, collection: Any | None = None) -> tuple[RetrievalResult, str]:
    """Full chain: retrieve → generate_answer."""
    if collection is None:
        collection = _get_collection()
    retrieval = retrieve(question, collection)
    # Assignment form: answer = generate_answer(question, retrieve(question))
    answer = generate_answer(question, retrieval)
    return retrieval, answer


# Day 10 baseline scores (from day10_retrieval_results.json / retrieval_test_results.md)
DAY10_BASELINE: list[dict[str, str]] = [
    {"question": TEST_QUESTIONS[0], "score": "good", "note": "Raw SQL deductibles for all plans"},
    {"question": TEST_QUESTIONS[1], "score": "partial", "note": "Raw SQL chunk with Gold 10% copay"},
    {"question": TEST_QUESTIONS[2], "score": "partial", "note": "Raw SQL chunk with Silver $300 premium"},
    {"question": TEST_QUESTIONS[3], "score": "good", "note": "Raw SQL claims list including C1001 Pending"},
    {"question": TEST_QUESTIONS[4], "score": "good", "note": "Plan facts; procedures not listed in KB"},
    {"question": TEST_QUESTIONS[5], "score": "good", "note": "Plan summary only — PT coverage not in KB"},
    {"question": TEST_QUESTIONS[6], "score": "partial", "note": "No maternity clause in KB"},
    {"question": TEST_QUESTIONS[7], "score": "partial", "note": "Generic plan data, no specialist policy"},
    {"question": TEST_QUESTIONS[8], "score": "good", "note": "Gold 10% copay; surgery not confirmed in KB"},
    {"question": TEST_QUESTIONS[9], "score": "partial", "note": "No enrollment/waiting-period docs in KB"},
]


def _assess_day11_answer(question: str, answer: str, context: str) -> str:
    """Light qualitative check vs Day 10 goals (sentences, no overclaim)."""
    a = answer.lower()
    refuses = "don't know" in a or "do not know" in a or "contact support" in a
    sentence_like = answer[:1].isupper() and ("." in answer or "?" in answer)
    overclaims_coverage = (
        any(w in question.lower() for w in ("covered", "coverage", "enrollment", "waiting"))
        and any(w in a for w in ("is covered", "are covered", "yes,"))
        and not refuses
        and "physical therapy" not in context.lower()
        and "maternity" not in context.lower()
        and "specialist" not in context.lower()
        and "enrollment" not in context.lower()
    )
    if refuses and sentence_like:
        return "improved - refuses when context lacks confirmation (well-formed)"
    if sentence_like and not overclaims_coverage:
        return "improved - well-formed sentence(s) from context, no clear overclaim"
    if overclaims_coverage:
        return "risk - may overstate coverage not clearly confirmed in context"
    return "mixed - check manually"


def run_day11_pipeline() -> list[dict[str, Any]]:
    """Run the same 10 Day 10 questions through retrieve → generate_answer and log answers."""
    print("=" * 80)
    print("DAY 11: RETRIEVE -> GENERATE_ANSWER PIPELINE")
    print("=" * 80)
    if _is_placeholder_key():
        print(
            "\n[note] GROQ_API_KEY is a placeholder - answers use grounded fallback. "
            "Set a real key in .env (or switch to Ollama placeholder client) for live LLM calls.\n"
        )

    collection = _get_collection()
    print(f"Chroma collection ready ({collection.count()} chunks)\n")

    logged: list[dict[str, Any]] = []

    for i, question in enumerate(TEST_QUESTIONS, start=1):
        print("-" * 80)
        print(f"Q{i}: {question}")
        retrieval, answer = answer_pipeline(question, collection)
        baseline = DAY10_BASELINE[i - 1]
        assessment = _assess_day11_answer(question, answer, retrieval.context_text)

        print(f"Day 10 baseline: {baseline['score'].upper()} - {baseline['note']}")
        print(f"Classification: {retrieval.classification.question_type}")
        print(f"Context preview: {retrieval.context_text[:220]}...")
        print(f"\nFinal answer:\n{answer}\n")
        print(f"vs Day 10: {assessment}")

        logged.append(
            {
                "num": i,
                "question": question,
                "day10_score": baseline["score"],
                "answer": answer,
                "assessment": assessment,
            }
        )

    print("\n" + "=" * 80)
    print("COMPARISON SUMMARY (Day 11 vs Day 10 baseline)")
    print("=" * 80)
    print(
        "Day 10 returned raw SQL/vector chunks. Day 11 turns them into grounded "
        "sentences, and should refuse (contact support) when coverage/process is "
        "not clearly confirmed in context - avoiding overstatement.\n"
    )
    for row in logged:
        print(f"Q{row['num']}: Day10={row['day10_score'].upper()} | {row['assessment']}")
        print(f"     -> {row['answer'][:160]}{'...' if len(row['answer']) > 160 else ''}\n")

    return logged


def run_streaming_demo() -> None:
    """One streaming call so tokens appear incrementally in the terminal."""
    print("\n" + "=" * 80)
    print("DAY 11: STREAMING DEMO")
    print("=" * 80)
    question = "What's the copay for the Gold plan?"
    collection = _get_collection()
    retrieval = retrieve(question, collection)
    print(f"Question: {question}\n")
    generate_answer_stream(question, retrieval)


def main() -> None:
    run_day11_pipeline()
    run_streaming_demo()


if __name__ == "__main__":
    main()
