# HEC RAG Chatbot

FastAPI RAG chatbot for answering questions from the HEC Undergraduate Education Policy PDF.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Create a `.env` file with your Google API key:

```text
GOOGLE_API_KEY=your_api_key_here
```

## Run

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000/docs
```

Example request body for `POST /ask`:

```json
{
  "question": "What is the minimum CGPA required for an Associate Degree?"
}
```

## Limitations

- Answers depend on retrieved PDF chunks, so missing retrieval can produce incomplete answers.
- The system only answers from `UGE-Policy.pdf`.
- PDF table extraction can lose formatting.
- The Chroma index is generated locally in `chroma_db/` and is not committed.
