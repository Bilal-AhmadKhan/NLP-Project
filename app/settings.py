"""Central configuration (read from .env) and factories for embeddings / LLM."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) else default


def _path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()


# ---- LLM (OpenRouter, OpenAI-compatible API) --------------------------------
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = _env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
# "openrouter/free" routes to whichever free model is available. You can pin a
# specific free model instead, e.g. "google/gemma-4-31b-it:free".
OPENROUTER_MODEL = _env("OPENROUTER_MODEL", "openrouter/free")
LLM_TEMPERATURE = float(_env("LLM_TEMPERATURE", "0.1"))

# ---- Embeddings -------------------------------------------------------------
# huggingface -> runs locally, free, no key needed (default)
# openrouter  -> uses OPENROUTER_API_KEY, e.g. EMBEDDING_MODEL=nvidia/nemotron-3.5-embed-1b:free
# google      -> uses GOOGLE_API_KEY, e.g. EMBEDDING_MODEL=models/gemini-embedding-001
EMBEDDING_PROVIDER = _env("EMBEDDING_PROVIDER", "huggingface").lower()
_DEFAULT_EMBEDDING_MODELS = {
    "huggingface": "BAAI/bge-small-en-v1.5",
    "openrouter": "nvidia/nemotron-3.5-embed-1b:free",
    "google": "models/gemini-embedding-001",
}
EMBEDDING_MODEL = _env("EMBEDDING_MODEL") or _DEFAULT_EMBEDDING_MODELS.get(EMBEDDING_PROVIDER, "")

# ---- Vector DB --------------------------------------------------------------
CHROMA_DIR = _path(_env("CHROMA_DIR", "chroma_db"))
CHROMA_COLLECTION = _env("CHROMA_COLLECTION", "fast_documents")
RETRIEVER_K = int(_env("RETRIEVER_K", "6"))
CHUNK_SIZE = int(_env("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(_env("CHUNK_OVERLAP", "200"))
SCRAPED_DATA_DIR = _path(_env("SCRAPED_DATA_DIR", "data/scraped"))
INDEX_INFO_FILE = CHROMA_DIR / "index_info.json"


def is_placeholder(value: str) -> bool:
    return not value or value.lower().startswith(("your_", "your-", "<", "sk-or-xxx", "changeme"))


class ConfigError(RuntimeError):
    """Raised when a required setting (usually an API key) is missing."""


@lru_cache(maxsize=1)
def get_embeddings():
    provider = EMBEDDING_PROVIDER
    if provider == "huggingface":
        from langchain_huggingface import HuggingFaceEmbeddings

        query_kwargs = {"normalize_embeddings": True}
        if "bge" in EMBEDDING_MODEL.lower() and "-en" in EMBEDDING_MODEL.lower():
            # BGE English models retrieve better with this query instruction.
            query_kwargs["prompt"] = "Represent this sentence for searching relevant passages: "
        return HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            encode_kwargs={"normalize_embeddings": True},
            query_encode_kwargs=query_kwargs,
        )
    if provider == "openrouter":
        if is_placeholder(OPENROUTER_API_KEY):
            raise ConfigError("EMBEDDING_PROVIDER=openrouter but OPENROUTER_API_KEY is not set in .env")
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=EMBEDDING_MODEL,
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_BASE_URL,
            check_embedding_ctx_length=False,  # OpenRouter takes raw strings, not tiktoken ids
            chunk_size=64,
        )
    if provider == "google":
        if is_placeholder(_env("GOOGLE_API_KEY")):
            raise ConfigError("EMBEDDING_PROVIDER=google but GOOGLE_API_KEY is not set in .env")
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        return GoogleGenerativeAIEmbeddings(model=EMBEDDING_MODEL)
    raise ConfigError(f"Unknown EMBEDDING_PROVIDER '{provider}' (use huggingface, openrouter or google)")


@lru_cache(maxsize=1)
def get_llm():
    if is_placeholder(OPENROUTER_API_KEY):
        raise ConfigError(
            "OPENROUTER_API_KEY is not set. Add your key to .env "
            "(get one free at https://openrouter.ai/keys)."
        )
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=OPENROUTER_MODEL,
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        temperature=LLM_TEMPERATURE,
        max_retries=2,
        timeout=90,
        default_headers={"X-Title": "FAST RAG Chatbot"},
    )


def get_vector_db():
    from langchain_chroma import Chroma

    return Chroma(
        persist_directory=str(CHROMA_DIR),
        collection_name=CHROMA_COLLECTION,
        embedding_function=get_embeddings(),
        collection_metadata={"hnsw:space": "cosine"},
    )


def read_index_info() -> dict:
    try:
        return json.loads(INDEX_INFO_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def embedding_mismatch() -> str | None:
    """Return a warning if the index was built with a different embedding model."""
    info = read_index_info().get(CHROMA_COLLECTION, {})
    built_with = (info.get("embedding_provider"), info.get("embedding_model"))
    if info and built_with != (EMBEDDING_PROVIDER, EMBEDDING_MODEL):
        return (
            f"Collection '{CHROMA_COLLECTION}' was built with {built_with[0]}:{built_with[1]} but the "
            f"current setting is {EMBEDDING_PROVIDER}:{EMBEDDING_MODEL}. "
            "Rebuild it with: python -m app.ingest --reset"
        )
    return None
