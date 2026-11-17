"""
Ask the RAG from the terminal.

    python -m app.ask "What courses are in semester 3 of BS CS?"
    python -m app.ask --search-only "fee structure"     # no LLM / API key needed
"""

import argparse
import sys

from . import settings
from .rag import EmptyIndexError, LLMError, search, user_question


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("question", nargs="+")
    p.add_argument("--search-only", action="store_true", help="only show retrieved chunks")
    p.add_argument("-k", type=int, default=None, help="number of chunks to retrieve")
    args = p.parse_args(argv)
    question = " ".join(args.question)

    try:
        if args.search_only:
            for i, hit in enumerate(search(question, args.k), start=1):
                print(f"--- [{i}] {hit['title']} ({hit['source']})")
                print(hit["content"][:800] + "\n")
            return 0
        result = user_question(question)
    except (EmptyIndexError, LLMError, settings.ConfigError) as exc:
        print(f"Error: {exc}")
        return 1

    print(result["answer"])
    print("\nSources:")
    for s in result["sources"]:
        print(f"  - {s.get('title') or ''} {s['source']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
