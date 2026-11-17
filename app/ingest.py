"""
Build / update the Chroma vector DB from scraped pages (and optionally PDFs).

    python -m app.ingest                                   # every data/scraped/*.jsonl
    python -m app.ingest --files data/scraped/lahore_cs.jsonl
    python -m app.ingest --pdf UGE-Policy.pdf              # also index the HEC policy PDF
    python -m app.ingest --reset                           # wipe the collection first

Re-ingesting the same source replaces its old chunks, so it is safe to re-run
after scraping again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from . import settings


def load_jsonl(path: Path) -> list[Document]:
    docs = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                print(f"  ! skipping bad JSON on line {line_no} of {path.name}")
                continue
            text = (rec.get("text") or "").strip()
            if not text:
                continue
            docs.append(Document(
                page_content=text,
                metadata={
                    "source": rec.get("url", str(path)),
                    "title": rec.get("title", ""),
                    "campus": rec.get("campus", ""),
                    "degree": rec.get("degree", ""),
                    "category": rec.get("category", ""),
                    "page": rec.get("title") or "web",
                    "dataset": path.stem,
                },
            ))
    return docs


def load_pdf(path: Path) -> list[Document]:
    from langchain_community.document_loaders import PyPDFLoader

    docs = PyPDFLoader(str(path)).load()
    for d in docs:
        d.metadata = {
            "source": path.name,
            "title": path.stem,
            "page": int(d.metadata.get("page", 0)) + 1,
            "category": "pdf",
            "dataset": path.stem,
        }
    return docs


def split(docs: list[Document]) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
        separators=["\n## ", "\n\n", "\n", ". ", " ", ""],
        add_start_index=True,
    )
    chunks = []
    for doc in docs:
        headings = [(m.start(), m.group(1).strip())
                    for m in re.finditer(r"^## (.+)$", doc.page_content, flags=re.M)]
        for c in splitter.split_documents([doc]):
            start = c.metadata.pop("start_index", 0)
            md = c.metadata
            # Prefix every chunk with where it came from (page title, campus,
            # degree) and the nearest section heading above it, so chunks such
            # as a list of names still say "Department of Computer Science Faculty".
            header = " | ".join(x for x in (md.get("title"), md.get("campus") and f"FAST {md['campus']}",
                                              md.get("degree")) if x)
            section = next((h for pos, h in reversed(headings) if pos <= start), "")
            if section and c.page_content.lstrip().startswith("## " + section):
                section = ""
            prefix = f"[{header}]" if header else ""
            if section:
                prefix += f"\nSection: {section}"
            c.page_content = f"{prefix}\n{c.page_content}" if prefix else c.page_content
            chunks.append(c)
    return chunks


def chunk_id(doc: Document, idx: int) -> str:
    h = hashlib.sha1(f"{doc.metadata.get('source')}|{doc.metadata.get('page')}|{idx}|{doc.page_content}".encode("utf-8"))
    return h.hexdigest()


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Index scraped data / PDFs into Chroma.")
    p.add_argument("--files", nargs="*", help="JSONL files from the scraper (default: all in data/scraped)")
    p.add_argument("--pdf", nargs="*", default=[], help="PDF files to index as well (e.g. UGE-Policy.pdf)")
    p.add_argument("--reset", action="store_true", help="delete the collection before indexing")
    args = p.parse_args(argv)

    files = [Path(f) for f in args.files] if args.files else sorted(settings.SCRAPED_DATA_DIR.glob("*.jsonl"))
    pdfs = [Path(f) for f in args.pdf]
    missing = [str(f) for f in files + pdfs if not f.exists()]
    if missing:
        print("File(s) not found: " + ", ".join(missing))
        return 2
    if not files and not pdfs:
        print(f"Nothing to index. Run the scraper first (python -m scraper.fast_scraper) "
              f"or pass --pdf. Looked in {settings.SCRAPED_DATA_DIR}")
        return 1

    print(f"Embeddings: {settings.EMBEDDING_PROVIDER}:{settings.EMBEDDING_MODEL}")
    print(f"Chroma: {settings.CHROMA_DIR} (collection '{settings.CHROMA_COLLECTION}')")

    if args.reset:
        from langchain_chroma import Chroma
        Chroma(persist_directory=str(settings.CHROMA_DIR),
               collection_name=settings.CHROMA_COLLECTION).delete_collection()
        print("Collection reset.")
    elif (warning := settings.embedding_mismatch()):
        print("ERROR: " + warning)
        return 1

    vector_db = settings.get_vector_db()

    docs: list[Document] = []
    for f in files:
        loaded = load_jsonl(f)
        print(f"  {f.name}: {len(loaded)} pages")
        docs.extend(loaded)
    for f in pdfs:
        loaded = load_pdf(f)
        print(f"  {f.name}: {len(loaded)} PDF pages")
        docs.extend(loaded)

    chunks = split(docs)
    print(f"Split into {len(chunks)} chunks; embedding...")

    # Replace old chunks of the same sources so re-runs don't create duplicates.
    sources = sorted({c.metadata["source"] for c in chunks})
    for i in range(0, len(sources), 100):
        vector_db._collection.delete(where={"source": {"$in": sources[i:i + 100]}})

    ids = [chunk_id(c, i) for i, c in enumerate(chunks)]
    batch = 128
    for i in range(0, len(chunks), batch):
        vector_db.add_documents(chunks[i:i + batch], ids=ids[i:i + batch])
        print(f"  indexed {min(i + batch, len(chunks))}/{len(chunks)}")

    total = vector_db._collection.count()
    info = settings.read_index_info()
    entry = info.get(settings.CHROMA_COLLECTION, {}) if not args.reset else {}
    datasets = entry.get("datasets", {})
    datasets.update(Counter(c.metadata["dataset"] for c in chunks))  # chunks per dataset
    info[settings.CHROMA_COLLECTION] = {
        "embedding_provider": settings.EMBEDDING_PROVIDER,
        "embedding_model": settings.EMBEDDING_MODEL,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_chunks": total,
        "datasets": datasets,
    }
    settings.INDEX_INFO_FILE.parent.mkdir(parents=True, exist_ok=True)
    settings.INDEX_INFO_FILE.write_text(json.dumps(info, indent=2), encoding="utf-8")

    print(f"\nDone. Collection '{settings.CHROMA_COLLECTION}' now holds {total} chunks.")
    print("Start the API with: python -m uvicorn app.main:app --reload")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
