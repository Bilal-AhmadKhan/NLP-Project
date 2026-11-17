"""Retrieval-augmented QA over the Chroma index (built by app.ingest)."""

from __future__ import annotations

from functools import lru_cache

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from . import settings


class EmptyIndexError(RuntimeError):
    """Raised when the vector DB has not been built yet."""


class LLMError(RuntimeError):
    """Raised when the OpenRouter call fails (bad key, rate limit, model gone...)."""


prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
You are a helpful assistant answering questions about FAST-NUCES
(National University of Computer and Emerging Sciences) programs, campuses,
admissions, fees, rules and departments.

Answer using only the supplied context. Be concise and specific; use bullet
points or a short table when listing courses, fees or criteria.
If the answer is not contained in the context, say:
"I do not know based on the provided documents."

Do not invent information.
""",
        ),
        (
            "human",
            """
Context:
{context}

Question:
{question}
""",
        ),
    ]
)


def format_docs(docs):
    return "\n\n".join(
        f"Source: {doc.metadata.get('source', 'Unknown')}\n"
        f"Title: {doc.metadata.get('title', '')}\n"
        f"Page: {doc.metadata.get('page', 'Unknown')}\n"
        f"Content: {doc.page_content}"
        for doc in docs
    )


@lru_cache(maxsize=1)
def get_vector_db():
    warning = settings.embedding_mismatch()
    if warning:
        raise settings.ConfigError(warning)
    return settings.get_vector_db()


def doc_count() -> int:
    return get_vector_db()._collection.count()


def retrieve(question: str, k: int | None = None):
    db = get_vector_db()
    if db._collection.count() == 0:
        raise EmptyIndexError(
            "The vector DB is empty. Build it first:\n"
            "  python -m scraper.fast_scraper --campus lahore --degree cs\n"
            "  python -m app.ingest"
        )
    return db.similarity_search(question, k=k or settings.RETRIEVER_K)


def _sources(docs):
    seen, out = set(), []
    for doc in docs:
        key = (doc.metadata.get("source", "Unknown"), doc.metadata.get("page", "Unknown"))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "source": key[0],
            "page": key[1],
            "title": doc.metadata.get("title") or None,
        })
    return out


def search(question: str, k: int | None = None):
    """Retrieval only (no LLM) - handy for debugging the index."""
    docs = retrieve(question, k)
    return [
        {"content": d.page_content, **{m: d.metadata.get(m) for m in ("source", "title", "page", "campus", "degree")}}
        for d in docs
    ]


def user_question(question: str):
    docs = retrieve(question)
    answer_chain = prompt | settings.get_llm() | StrOutputParser()
    try:
        answer = answer_chain.invoke(
            {
                "context": format_docs(docs),
                "question": question,
            }
        )
    except Exception as exc:  # openai.APIError subclasses, network errors, ...
        status = getattr(exc, "status_code", None)
        hint = {
            401: "check OPENROUTER_API_KEY in .env",
            402: "this model needs credits - pick a ':free' model",
            404: f"model '{settings.OPENROUTER_MODEL}' not found - set another OPENROUTER_MODEL",
            429: "free-tier rate limit hit - wait a bit or switch OPENROUTER_MODEL",
        }.get(status, "see the server log for details")
        raise LLMError(f"OpenRouter request failed ({status or type(exc).__name__}): {hint}. {exc}") from exc
    return {
        "answer": answer.strip(),
        "sources": _sources(docs),
    }
