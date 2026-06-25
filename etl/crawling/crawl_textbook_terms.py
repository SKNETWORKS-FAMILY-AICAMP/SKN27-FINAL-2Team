from __future__ import annotations

import argparse
import csv
import re
import time
import urllib.parse
import urllib.request
from html import unescape
from html.parser import HTMLParser
from pathlib import Path


BASE_URL = "https://contents.history.go.kr"
DEFAULT_OUTPUT = Path("etl/raw_data/교과서_용어해설/textbook_terms.csv")
ERAS = {
    "0101": "삼국 이전",
    "0102": "삼국 시대",
    "0103": "통일 신라와 발해",
    "0104": "고려 시대",
    "0105": "조선 시대",
    "0106": "근대",
}


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value)).strip()


class TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data)

    def text(self) -> str:
        return clean(" ".join(self.parts))


def strip_tags(html: str) -> str:
    parser = TextParser()
    parser.feed(html)
    return parser.text()


def fetch(url: str, timeout: int, retries: int) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last_error: Exception | None = None
    for _ in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read().decode(response.headers.get_content_charset() or "utf-8", "ignore")
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"request failed: {last_error}")


def list_url(tree_id: str, page: int, page_unit: int) -> str:
    query = urllib.parse.urlencode({"treeId": tree_id, "pageIndex": page, "pageUnit": page_unit})
    return f"{BASE_URL}/front/tg/list.do?{query}"


def absolute_url(path: str) -> str:
    return urllib.parse.urljoin(BASE_URL, unescape(path))


def total_count(html: str) -> int:
    match = re.search(r"<h2>.*?총\s*([0-9,]+)\s*건", html, re.S)
    return int(match.group(1).replace(",", "")) if match else 0


def parse_list(html: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for match in re.finditer(r'<a href="([^"]*?/front/tg/view\.do\?[^"]+)".*?>(.*?)</a>', html, re.S):
        href, title_html = match.groups()
        title = strip_tags(title_html)
        if not title:
            continue
        source_url = absolute_url(href)
        term_id = urllib.parse.parse_qs(urllib.parse.urlsplit(source_url).query).get("levelId", [""])[0]
        rows.append({"term_id": term_id, "term_name": title, "source_url": source_url})
    return rows


def parse_detail(html: str) -> tuple[str, str]:
    title_match = re.search(r'<h[1-4][^>]*>(.*?)</h[1-4]>', html, re.S)
    title = strip_tags(title_match.group(1)) if title_match else ""
    body_match = re.search(r'<div[^>]+class="[^"]*(?:view|content|cont|txt)[^"]*"[^>]*>(.*?)</div>\s*(?:</div>|<script|<!--)', html, re.S)
    body = strip_tags(body_match.group(1)) if body_match else strip_tags(html)
    return title, body


def crawl(output: Path, delay: float, timeout: int, page_unit: int, retries: int) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    for tree_id, era in ERAS.items():
        first = fetch(list_url(tree_id, 1, page_unit), timeout, retries)
        pages = max(1, (total_count(first) + page_unit - 1) // page_unit)
        for page in range(1, pages + 1):
            html = first if page == 1 else fetch(list_url(tree_id, page, page_unit), timeout, retries)
            for item in parse_list(html):
                key = item["term_id"] or item["source_url"]
                if key in seen:
                    continue
                seen.add(key)
                detail_html = fetch(item["source_url"], timeout, retries)
                detail_title, content = parse_detail(detail_html)
                rows.append(
                    {
                        "term_id": item["term_id"],
                        "term_name": detail_title or item["term_name"],
                        "era": era,
                        "tree_id": tree_id,
                        "content": content,
                        "source_url": item["source_url"],
                    }
                )
                time.sleep(delay)
            time.sleep(delay)

    with output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["term_id", "term_name", "era", "tree_id", "content", "source_url"])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl textbook glossary terms by era.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--page-unit", type=int, default=100)
    args = parser.parse_args()
    print(f"saved={args.output} rows={crawl(args.output, args.delay, args.timeout, args.page_unit, args.retries)}")


if __name__ == "__main__":
    main()
