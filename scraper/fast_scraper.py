"""
Extract degree- and campus-specific information from the FAST-NUCES websites.

Examples
--------
    # Computer Science @ Lahore (default)
    python -m scraper.fast_scraper

    # Any other combination
    python -m scraper.fast_scraper --campus islamabad --degree se
    python -m scraper.fast_scraper --campus karachi --degree ds --max-pages 80

    # Custom program codes / extra keywords
    python -m scraper.fast_scraper --campus lahore --degree cs --programs "BS(CS)" "MS(CS)" --keywords "artificial intelligence"

    # See what is configured
    python -m scraper.fast_scraper --list

Output
------
data/scraped/<campus>_<degree>.jsonl  - one JSON record per page (used by app.ingest)
data/scraped/<campus>_<degree>.md     - the same content, human readable
"""

from __future__ import annotations

import argparse
import heapq
import io
import json
import logging
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin, urlparse, urldefrag, unquote, quote

import requests
from bs4 import BeautifulSoup, Comment, NavigableString

from .config import (
    CAMPUSES,
    CAMPUS_HQ_PAGES,
    DEGREES,
    GENERAL_CAMPUS_KEYWORDS,
    GENERAL_HQ_PAGES,
    HQ_SITE,
    SKIP_EXTENSIONS,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = PROJECT_ROOT / "data" / "scraped"
USER_AGENT = "Mozilla/5.0 (compatible; FAST-RAG-StudentProject/1.0; educational use)"
MIN_PAGE_CHARS = 150

log = logging.getLogger("fast_scraper")

# News/event/gallery pages are crawled last and capped by --max-news.
LOW_PRIORITY_RE = re.compile(r"news|event|blog|gallery|photo|/\d{4}/\d{2}/")

BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "aside", "li", "ul", "ol",
    "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "dl", "dt", "dd",
    "blockquote", "pre", "figure", "figcaption", "address",
}
# Tags whose content is never useful text.
DROP_TAGS = ["script", "style", "noscript", "svg", "iframe", "form", "button",
             "header", "footer", "nav", "select", "input", "template"]
# class/id tokens that mark navigation / chrome.
CHROME_RE = re.compile(
    r"^(nav|navbar|navigation|menu|main-menu|menuzord.*|mega-menu|dropdown-menu|"
    r"footer.*|site-footer|breadcrumbs?|sidebar|side-bar|top-?bar|header-top|"
    r"social.*|copyright|cookie.*|skip-link|back-to-top)$",
    re.I,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def normalise(text: str) -> str:
    """Lower-case and turn every non-alphanumeric run into one space."""
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "


def compile_keywords(keywords: list[str]) -> list[re.Pattern]:
    pats = []
    for kw in keywords:
        kw_norm = normalise(kw).strip()
        if kw_norm:
            pats.append(re.compile(r"(?<![a-z0-9])" + re.escape(kw_norm) + r"(?![a-z0-9])"))
    return pats


def keyword_hits(text: str, patterns: list[re.Pattern]) -> int:
    norm = normalise(text)
    return sum(1 for p in patterns if p.search(norm))


def canonical_url(url: str) -> str:
    """Drop fragments, normalise scheme/host and encode spaces etc."""
    url, _ = urldefrag(url.strip())
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = quote(unquote(parsed.path), safe="/()%:@!$&'*+,;=-._~") or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"https://{host}{path}{query}"


def host_key(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


@dataclass(order=True)
class QueueItem:
    priority: tuple
    url: str = field(compare=False)
    depth: int = field(compare=False)
    category: str = field(compare=False)
    reason: str = field(compare=False)


@dataclass
class PageRecord:
    url: str
    title: str
    text: str
    campus: str
    degree: str
    category: str
    fetched_at: str
    content_type: str = "html"


# --------------------------------------------------------------------------- #
# HTML -> text
# --------------------------------------------------------------------------- #

def extract_links(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        if urlparse(absolute).scheme not in ("http", "https"):
            continue
        anchor = a.get_text(" ", strip=True) or a.get("title", "")
        links.append((absolute, anchor))
    return links


def _table_to_text(table) -> str:
    """Render a table as "a | b | c" rows.

    A single-cell row (e.g. "Semester 3") is treated as a group label and
    prefixed to the rows under it, so every row stays meaningful on its own
    after chunking: "Semester 3 | 3 | CS2001 Data Structures | 3+1".
    """
    rows, label = [], ""
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        cells = [re.sub(r"\s+", " ", c) for c in cells if c]
        if not cells:
            continue
        if len(cells) == 1 and len(cells[0]) <= 60:
            label = cells[0]
            rows.append(label)
            continue
        is_header = tr.find("td") is None
        if label and not is_header and cells[0] != label:
            cells = [label] + cells
        rows.append(" | ".join(cells))
    return "\n".join(rows)


def html_to_text(html: str, url: str) -> tuple[str, str, list[tuple[str, str]]]:
    """Return (title, clean_text, links)."""
    soup = BeautifulSoup(html, "lxml")
    links = extract_links(soup, url)  # collect before stripping navigation

    title = ""
    if soup.title and soup.title.string:
        title = re.sub(r"\s+", " ", soup.title.string).strip()

    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()
    for tag in soup.find_all(True):
        if getattr(tag, "decomposed", False):
            continue
        attrs = getattr(tag, "attrs", None) or {}
        tokens = list(attrs.get("class") or []) + ([attrs["id"]] if attrs.get("id") else [])
        if any(CHROME_RE.match(t) for t in tokens):
            tag.decompose()

    # Prefer the first <h1>/<h2> as title if <title> is generic.
    heading = soup.find(["h1", "h2"])
    if heading:
        h = heading.get_text(" ", strip=True)
        if h and (not title or len(title) < 4 or title.lower() in ("home", "index")):
            title = h

    # Source-code line wraps inside paragraphs are not real line breaks.
    for s in soup.find_all(string=True):
        if "\n" in s and s.parent is not None and s.parent.name != "pre":
            s.replace_with(NavigableString(re.sub(r"\s*\n\s*", " ", str(s))))

    # Tables -> "a | b | c" rows (keeps study plans / fee tables readable).
    for table in soup.find_all("table"):
        table.replace_with(NavigableString("\n" + _table_to_text(table) + "\n"))

    # Mark headings so the chunker/LLM sees structure.
    for h in soup.find_all(["h1", "h2", "h3", "h4"]):
        text = h.get_text(" ", strip=True)
        h.clear()
        if text:
            h.append(NavigableString(f"## {text}"))

    for br in soup.find_all("br"):
        br.replace_with(NavigableString("\n"))
    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before(NavigableString("\n"))
        tag.insert_after(NavigableString("\n"))

    root = soup.body or soup
    raw = root.get_text()
    lines = []
    for line in raw.split("\n"):
        line = re.sub(r"\s+", " ", line.replace("\xa0", " ")).strip()
        if not line or line in ("-->", "<!--", "|", ">", "»"):
            continue
        if lines and lines[-1] == line:
            continue
        lines.append(line)
    return title, "\n".join(lines), links


def pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            txt = page.extract_text() or ""
        except Exception:  # pragma: no cover - malformed pages
            txt = ""
        if txt.strip():
            parts.append(f"[Page {i}]\n{txt.strip()}")
    return "\n\n".join(parts)


def remove_boilerplate(records: list[PageRecord], min_pages: int = 4, ratio: float = 0.5) -> None:
    """Drop lines that repeat on most pages of the same host (menus, footers)."""
    by_host: dict[str, list[PageRecord]] = {}
    for r in records:
        if r.content_type == "html":
            by_host.setdefault(host_key(r.url), []).append(r)

    for host, recs in by_host.items():
        if len(recs) < min_pages:
            continue
        df = Counter()
        sections: dict[str, set[str]] = {}
        for r in recs:
            segs = [x for x in urlparse(r.url).path.lower().split("/") if x][:2]
            section = "/".join(segs) or "/"
            for line in set(r.text.split("\n")):
                df[line] += 1
                sections.setdefault(line, set()).add(section)
        threshold = max(3, int(len(recs) * ratio))
        # Real boilerplate (menus/footers) repeats across *different* parts of
        # the site; lines like "Assistant Professor" only repeat inside one
        # section (e.g. faculty profiles) and must be kept.
        common = {line for line, n in df.items()
                  if n >= threshold and len(sections[line]) >= 3}
        if not common:
            continue
        log.debug("Removing %d boilerplate lines from %s", len(common), host)
        for r in recs:
            r.text = "\n".join(l for l in r.text.split("\n") if l not in common)


# --------------------------------------------------------------------------- #
# Crawler
# --------------------------------------------------------------------------- #

class FastScraper:
    def __init__(
        self,
        campus_key: str,
        degree_key: str,
        programs: list[str],
        keywords: list[str],
        campus_site: str,
        max_pages: int = 80,
        max_depth: int = 3,
        delay: float = 0.5,
        timeout: float = 25.0,
        include_general: bool = True,
        include_campus_hq: bool = True,
        include_pdfs: bool = False,
        max_pdfs: int = 10,
        max_news: int = 10,
        respect_robots: bool = True,
    ):
        self.campus_key = campus_key
        self.campus = CAMPUSES.get(campus_key, {"name": campus_key, "hq_slug": None})
        self.degree_key = degree_key
        self.degree_name = DEGREES.get(degree_key, {}).get("name", degree_key)
        self.programs = programs
        self.keyword_patterns = compile_keywords(keywords)
        self.general_patterns = compile_keywords(GENERAL_CAMPUS_KEYWORDS)
        self.campus_site = campus_site.rstrip("/") + "/"
        self.campus_host = host_key(self.campus_site)
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.delay = delay
        self.timeout = timeout
        self.include_general = include_general
        self.include_campus_hq = include_campus_hq
        self.include_pdfs = include_pdfs
        self.max_pdfs = max_pdfs
        self.max_news = max_news
        self.news_count = 0
        self.respect_robots = respect_robots

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._last_request = 0.0
        self.seen: set[str] = set()
        self.records: list[PageRecord] = []
        self.failed: list[tuple[str, str]] = []
        self.pdf_count = 0

    # ------------------------------------------------------------------ HTTP
    def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in self._robots:
            rp = robotparser.RobotFileParser()
            try:
                resp = self.session.get(base + "/robots.txt", timeout=10)
                if resp.status_code == 200 and "html" not in resp.headers.get("Content-Type", ""):
                    rp.parse(resp.text.splitlines())
                else:
                    rp = None
            except requests.RequestException:
                rp = None
            self._robots[base] = rp
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    def _get(self, url: str) -> requests.Response | None:
        if not self._allowed(url):
            log.info("  robots.txt disallows %s", url)
            self.failed.append((url, "robots.txt"))
            return None
        wait = self.delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            try:
                self._last_request = time.monotonic()
                resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
                if resp.status_code >= 500 and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if resp.status_code != 200:
                    self.failed.append((url, f"HTTP {resp.status_code}"))
                    return None
                return resp
            except requests.RequestException as exc:
                if attempt == 2:
                    self.failed.append((url, type(exc).__name__))
                    log.warning("  failed %s (%s)", url, type(exc).__name__)
                    return None
                time.sleep(1.5 * (attempt + 1))
        return None

    # --------------------------------------------------------------- scoring
    def _relevance(self, url: str, anchor: str = "") -> int:
        path = unquote(urlparse(url).path + " " + urlparse(url).query)
        return keyword_hits(path + " " + anchor, self.keyword_patterns)

    def _is_general(self, url: str, anchor: str = "") -> bool:
        path = unquote(urlparse(url).path)
        return keyword_hits(path + " " + anchor, self.general_patterns) > 0

    @staticmethod
    def _is_low_priority(url: str) -> bool:
        return bool(LOW_PRIORITY_RE.search(urlparse(url).path.lower()))

    @staticmethod
    def _skippable(url: str) -> bool:
        path = urlparse(url).path.lower()
        return path.endswith(SKIP_EXTENSIONS) or any(
            s in path for s in ("/wp-admin", "/wp-login", "/login", "/logout", "/feed", "/wp-json")
        )

    # ---------------------------------------------------------------- crawl
    def build_seeds(self) -> list[QueueItem]:
        seeds: list[QueueItem] = []
        for code in self.programs:
            seeds.append(QueueItem((-1, 0), f"{HQ_SITE}/Program/{code}", 0, "program", "program page"))
        if self.include_general:
            for path in GENERAL_HQ_PAGES:
                seeds.append(QueueItem((-1, 1), HQ_SITE + path, 0, "university", "general HQ page"))
        slug = self.campus.get("hq_slug")
        if self.include_campus_hq and slug:
            for path in CAMPUS_HQ_PAGES:
                seeds.append(QueueItem((-1, 2), HQ_SITE + path.format(slug=slug), 0, "campus", "campus HQ page"))
        seeds.append(QueueItem((-1, 3), self.campus_site, 0, "campus", "campus home"))
        return seeds

    def crawl(self) -> list[PageRecord]:
        heap: list[QueueItem] = []
        for s in self.build_seeds():
            heapq.heappush(heap, s)
        counter = len(heap)  # tie-breaker keeps insertion order stable
        crawled = 0  # non-seed pages fetched from the campus site
        root_dirs = {"/"}

        while heap:
            item = heapq.heappop(heap)
            url = canonical_url(item.url)
            if url in self.seen:
                continue
            is_seed = item.depth == 0
            if not is_seed:
                if crawled >= self.max_pages:
                    break
                if self._is_low_priority(url):
                    if self.news_count >= self.max_news:
                        continue
                    self.news_count += 1
                crawled += 1
            self.seen.add(url)

            log.info("[%3d] d=%d %-10s %s", len(self.seen), item.depth, item.category, url)
            resp = self._get(url)
            if resp is None:
                continue
            final_url = canonical_url(resp.url)
            if final_url != url and final_url in self.seen and not is_seed:
                continue
            self.seen.add(final_url)
            if is_seed and host_key(final_url) == self.campus_host:
                # Directory of the campus homepage (after redirects) is not a "section".
                root_dirs.add(urlparse(final_url).path.rsplit("/", 1)[0] + "/")
            ctype = resp.headers.get("Content-Type", "").lower()

            if "pdf" in ctype or final_url.lower().endswith(".pdf"):
                self._handle_pdf(final_url, resp.content, item.category)
                continue
            if "html" not in ctype:
                continue

            resp.encoding = resp.encoding if resp.encoding and resp.encoding.lower() != "iso-8859-1" else resp.apparent_encoding
            title, text, links = html_to_text(resp.text, final_url)
            page_relevant = (
                is_seed
                or item.reason in ("relevant link", "same section")
                or keyword_hits(final_url + " " + title, self.keyword_patterns) > 0
            )
            if len(text) >= MIN_PAGE_CHARS:
                self.records.append(PageRecord(
                    url=final_url, title=title or final_url, text=text,
                    campus=self.campus["name"], degree=self.degree_name,
                    category=item.category,
                    fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ))

            # Only expand links from the campus site (HQ pages are fixed seeds).
            if host_key(final_url) != self.campus_host or item.depth >= self.max_depth:
                continue
            parent_dir = urlparse(final_url).path.rsplit("/", 1)[0] + "/"
            # Follow un-labelled sibling links only inside a degree-specific
            # folder such as /fsc/ or /computer-science/ (not /Academics/).
            section_relevant = (
                page_relevant and item.depth > 0 and parent_dir not in root_dirs
                and keyword_hits(unquote(parent_dir), self.keyword_patterns) > 0
            )
            for link, anchor in links:
                link_c = canonical_url(link)
                if link_c in self.seen or self._skippable(link_c):
                    continue
                is_pdf = urlparse(link_c).path.lower().endswith(".pdf")
                rel = self._relevance(link_c, anchor)
                if is_pdf:
                    if self.include_pdfs and rel > 0:
                        counter += 1
                        heapq.heappush(heap, QueueItem((0, item.depth + 1, -rel, counter), link_c,
                                                       item.depth + 1, item.category, "relevant pdf"))
                    continue
                if host_key(link_c) != self.campus_host:
                    continue
                reason = None
                if rel > 0:
                    reason = "relevant link"
                elif section_relevant and urlparse(link_c).path.startswith(parent_dir):
                    reason = "same section"
                elif item.depth == 0 and self._is_general(link_c, anchor):
                    reason = "general campus"
                if reason is None:
                    continue
                category = "department" if reason != "general campus" else "campus"
                counter += 1
                # Lower tuple = fetched first: news/events last, then shallow
                # pages, then the ones with the most keyword hits.
                prio = (int(self._is_low_priority(link_c)), item.depth + 1, -rel, counter)
                heapq.heappush(heap, QueueItem(prio, link_c, item.depth + 1, category, reason))

        remove_boilerplate(self.records)
        self.records = [r for r in self.records if len(r.text) >= MIN_PAGE_CHARS]
        return self.records

    def _handle_pdf(self, url: str, data: bytes, category: str) -> None:
        if not self.include_pdfs or self.pdf_count >= self.max_pdfs:
            return
        if len(data) > 20 * 1024 * 1024:
            log.info("  skipping large PDF %s", url)
            return
        try:
            text = pdf_to_text(data)
        except Exception as exc:
            self.failed.append((url, f"PDF parse error: {exc}"))
            return
        if len(text) < MIN_PAGE_CHARS:
            return
        self.pdf_count += 1
        self.records.append(PageRecord(
            url=url, title=unquote(Path(urlparse(url).path).name), text=text,
            campus=self.campus["name"], degree=self.degree_name, category=category,
            fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            content_type="pdf",
        ))


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

def write_outputs(records: list[PageRecord], out_dir: Path, stem: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / f"{stem}.jsonl"
    md_path = out_dir / f"{stem}.md"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    with md_path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(f"# {r.title}\n\n")
            fh.write(f"- URL: {r.url}\n- Campus: {r.campus}\n- Degree: {r.degree}\n- Category: {r.category}\n\n")
            fh.write(r.text + "\n\n---\n\n")
    return jsonl_path, md_path


def list_config() -> None:
    print("Campuses:")
    for k, v in CAMPUSES.items():
        print(f"  {k:<20} {v['site']}")
    print("\nDegrees:")
    for k, v in DEGREES.items():
        print(f"  {k:<10} {v['name']:<26} programs: {', '.join(v['programs'])}")


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Scrape FAST-NUCES pages for a given campus and degree into JSONL for the RAG index.")
    p.add_argument("--campus", default="lahore", help=f"campus key ({', '.join(CAMPUSES)}) [default: lahore]")
    p.add_argument("--degree", default="cs", help=f"degree key ({', '.join(DEGREES)}) [default: cs]")
    p.add_argument("--programs", nargs="+", help='override HQ program codes, e.g. "BS(CS)" "MS(CS)"')
    p.add_argument("--keywords", nargs="+", default=[], help="extra keywords to find department pages")
    p.add_argument("--campus-url", help="override the campus website URL")
    p.add_argument("--max-pages", type=int, default=80, help="max pages to crawl on the campus site (seeds not counted)")
    p.add_argument("--max-depth", type=int, default=3, help="max link depth on the campus site")
    p.add_argument("--max-news", type=int, default=10, help="max news/event pages to include")
    p.add_argument("--delay", type=float, default=0.5, help="seconds between requests (be polite)")
    p.add_argument("--no-general", action="store_true", help="skip university-wide admissions/fee/rules pages")
    p.add_argument("--no-campus-hq", action="store_true", help="skip nu.edu.pk/Campus/<campus>/... pages")
    p.add_argument("--include-pdfs", action="store_true", help="also download relevant linked PDFs")
    p.add_argument("--ignore-robots", action="store_true", help="do not check robots.txt")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="output directory")
    p.add_argument("--list", action="store_true", help="list configured campuses and degrees, then exit")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(message)s", stream=sys.stdout)

    if args.list:
        list_config()
        return 0

    campus_key = args.campus.lower()
    degree_key = args.degree.lower()
    if campus_key not in CAMPUSES and not args.campus_url:
        print(f"Unknown campus '{args.campus}'. Use --list, or pass --campus-url.")
        return 2
    if degree_key not in DEGREES and not args.programs:
        print(f"Unknown degree '{args.degree}'. Use --list, or pass --programs and --keywords.")
        return 2

    degree_cfg = DEGREES.get(degree_key, {"programs": [], "keywords": []})
    programs = args.programs or degree_cfg["programs"]
    keywords = list(dict.fromkeys(degree_cfg["keywords"] + args.keywords))
    campus_site = args.campus_url or CAMPUSES[campus_key]["site"]

    scraper = FastScraper(
        campus_key=campus_key,
        degree_key=degree_key,
        programs=programs,
        keywords=keywords,
        campus_site=campus_site,
        max_pages=args.max_pages,
        max_depth=args.max_depth,
        delay=args.delay,
        include_general=not args.no_general,
        include_campus_hq=not args.no_campus_hq,
        include_pdfs=args.include_pdfs,
        max_news=args.max_news,
        respect_robots=not args.ignore_robots,
    )
    print(f"Scraping {scraper.degree_name} @ FAST {scraper.campus['name']} "
          f"(programs: {', '.join(programs)}; site: {campus_site})\n")
    records = scraper.crawl()

    stem = f"{campus_key}_{degree_key}"
    jsonl_path, md_path = write_outputs(records, Path(args.out_dir), stem)

    by_cat = Counter(r.category for r in records)
    total_chars = sum(len(r.text) for r in records)
    print(f"\nSaved {len(records)} pages ({total_chars:,} chars) -> {jsonl_path}")
    print("  by category: " + ", ".join(f"{k}={v}" for k, v in sorted(by_cat.items())))
    print(f"  readable copy: {md_path}")
    if scraper.failed:
        print(f"  {len(scraper.failed)} URLs failed/skipped:")
        for url, why in scraper.failed[:15]:
            print(f"    - {url} ({why})")
    if not records:
        print("No pages were extracted - check your network connection / campus URL.")
        return 1
    print("\nNext: python -m app.ingest   (builds/updates the Chroma vector DB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
