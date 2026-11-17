# FAST-NUCES RAG Chatbot

FastAPI RAG chatbot that answers questions about FAST-NUCES programs, built on a
**Chroma** vector DB. Data is scraped from the official FAST websites by a
configurable extractor (any **degree** × **campus**, default: Computer Science @
Lahore). The LLM runs through **OpenRouter** (free models work); embeddings run
locally for free by default. The original HEC Undergraduate Education Policy PDF
can still be indexed too.

```
scraper/fast_scraper.py  ->  data/scraped/<campus>_<degree>.jsonl
app/ingest.py            ->  chroma_db/  (Chroma vector DB)
app/main.py (FastAPI)    ->  POST /ask, POST /search, GET /health
```

## 1. Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Then open `.env` and replace the placeholder:

```text
OPENROUTER_API_KEY=your_openrouter_api_key_here   # https://openrouter.ai/keys
OPENROUTER_MODEL=openrouter/free                  # or a specific ":free" model id
```

`openrouter/free` lets OpenRouter pick any available free model. To pin one, use
any id ending in `:free` from <https://openrouter.ai/models?max_price=0>.

Only the `/ask` endpoint needs the key. Scraping, indexing and `/search` work without it.

## 2. Scrape FAST data

```powershell
# Computer Science @ Lahore (default)
.\.venv\Scripts\python.exe -m scraper.fast_scraper

# Any other degree / campus
.\.venv\Scripts\python.exe -m scraper.fast_scraper --campus islamabad --degree se
.\.venv\Scripts\python.exe -m scraper.fast_scraper --campus karachi --degree ds

# See what's configured
.\.venv\Scripts\python.exe -m scraper.fast_scraper --list
```

What it collects:

| Source | Example |
| --- | --- |
| Program pages on nu.edu.pk | `/Program/BS(CS)`, `/Program/MS(CS)`, `/Program/PhD(CS)`: mission, study plan, credit hours, PLOs |
| University-wide pages | eligibility, admission schedule, test pattern, fee structure, scholarships, FAQ, grading, rules |
| Campus pages on nu.edu.pk | `/Campus/Lahore/Research`, `/Campus/Lahore/PhdSupervisors` |
| Campus website (crawled) | department home, faculty list and profiles, research, FYP, about, societies, support |

The campus crawler works the same way for every campus. It starts at the campus
homepage and follows links whose URL or text matches the degree's keywords (Lahore's
`/fsc/`, Islamabad's `Faculty-DCS.php`, Peshawar's `/cs-faculty/`, ...). News and
event pages are fetched last and capped.

Useful options:

| Option | Meaning |
| --- | --- |
| `--campus` / `--degree` | keys from `scraper/config.py` (`--list` shows them) |
| `--programs "BS(CS)" "MS(CS)"` | override which `nu.edu.pk/Program/...` pages to pull |
| `--keywords "machine learning"` | extra keywords for finding department pages |
| `--campus-url URL` | override the campus site |
| `--max-pages 80` / `--max-depth 3` / `--max-news 10` | crawl budget |
| `--include-pdfs` | also download relevant linked PDFs |
| `--no-general` / `--no-campus-hq` | skip university-wide / campus HQ pages |

To add a new degree or campus, add an entry to `DEGREES` / `CAMPUSES` in
[scraper/config.py](scraper/config.py).

Output goes to `data/scraped/<campus>_<degree>.jsonl`, with a readable `.md` copy next to it.

## 3. Build the vector DB (Chroma)

```powershell
.\.venv\Scripts\python.exe -m app.ingest                      # all data/scraped/*.jsonl
.\.venv\Scripts\python.exe -m app.ingest --pdf UGE-Policy.pdf  # + the HEC policy PDF
.\.venv\Scripts\python.exe -m app.ingest --reset               # rebuild from scratch
```

Re-running replaces the old chunks of the same pages, so you can re-scrape and
re-ingest at any time. Index metadata is written to `chroma_db/index_info.json`.

### Embeddings

| `EMBEDDING_PROVIDER` | `EMBEDDING_MODEL` (default) | Key |
| --- | --- | --- |
| `huggingface` (default) | `BAAI/bge-small-en-v1.5` (runs locally, free) | none |
| `openrouter` | `nvidia/nemotron-3.5-embed-1b:free` | `OPENROUTER_API_KEY` |
| `google` | `models/gemini-embedding-001` | `GOOGLE_API_KEY` |

If you change the provider or model, rebuild with `python -m app.ingest --reset`.
The app refuses to query an index built with a different model.

## 4. Run

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000/docs>.

| Endpoint | Purpose |
| --- | --- |
| `POST /ask` | RAG answer + sources (needs `OPENROUTER_API_KEY`) |
| `POST /search` | retrieved chunks only, no LLM (good for debugging retrieval) |
| `GET /health` | chunk count, embedding/LLM config, whether the key is set |

Example body:

```json
{ "question": "Which courses are taught in semester 3 of BS Computer Science?" }
```

From the terminal:

```powershell
.\.venv\Scripts\python.exe -m app.ask "What is the fee per credit hour?"
.\.venv\Scripts\python.exe -m app.ask --search-only "Who is the HOD of Computer Science at Lahore?"
```

## Limitations

- Answers depend on retrieved chunks, so a missed retrieval can produce an incomplete answer.
- Website content changes. Re-run the scraper and `app.ingest` to refresh.
- Some campus pages are stale or return errors on FAST's side (e.g. Lahore `/campusLife/` returns HTTP 500). The scraper skips them and lists them at the end.
- Free OpenRouter models are rate-limited and change over time. If one stops working, set another `:free` model in `OPENROUTER_MODEL`.
- `chroma_db/` and `.env` are generated locally and not committed.
