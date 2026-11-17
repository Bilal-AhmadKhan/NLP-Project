from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette import status

from . import settings
from .rag import EmptyIndexError, LLMError, doc_count, search, user_question

app = FastAPI(title="FAST RAG Chatbot")


class qin(BaseModel):
    question: str


class Source(BaseModel):
    source: str
    page: int | str
    title: str | None = None


class qresponse(BaseModel):
    answer: str
    sources: list[Source]


class SearchHit(BaseModel):
    content: str
    source: str | None = None
    title: str | None = None
    page: int | str | None = None
    campus: str | None = None
    degree: str | None = None


def _handle(fn, *args):
    try:
        return fn(*args)
    except EmptyIndexError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except settings.ConfigError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    except LLMError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@app.get('/')
def root():
    return RedirectResponse(url='/docs', status_code=status.HTTP_302_FOUND)


@app.get('/health')
def health():
    return {
        "collection": settings.CHROMA_COLLECTION,
        "chunks": _handle(doc_count),
        "embeddings": f"{settings.EMBEDDING_PROVIDER}:{settings.EMBEDDING_MODEL}",
        "llm": settings.OPENROUTER_MODEL,
        "llm_key_configured": not settings.is_placeholder(settings.OPENROUTER_API_KEY),
    }


@app.post('/ask', response_model=qresponse)
def ask_chatbot(question: qin):
    return _handle(user_question, question.question)


@app.post('/search', response_model=list[SearchHit])
def search_chunks(question: qin):
    """Return the retrieved chunks only (no LLM call, no API key needed)."""
    return _handle(search, question.question)
