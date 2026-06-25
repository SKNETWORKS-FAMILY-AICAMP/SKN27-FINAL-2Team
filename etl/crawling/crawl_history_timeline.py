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
DEFAULT_OUTPUT = Path("etl/raw_data/한국사연대기_연표/history_timeline.csv")
AGES = {
    6: "고대",
    5: "고려",
    4: "조선",
    3: "근대",
    2: "현대",
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


def fetch_age(age_id: int, timeout: int) -> str:
    url = f"{BASE_URL}/front/kc/setItemsKCList.do?{urllib.parse.urlencode({'age_id': age_id})}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode(response.headers.get_content_charset() or "utf-8", "ignore")


def parse_age(age_id: int, html: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current_year = ""
    for chunk in re.split(r'(<em class="year"><span>.*?</span></em>)', html, flags=re.S):
        year_match = re.search(r'<em class="year"><span>(.*?)</span></em>', chunk, re.S)
        if year_match:
            current_year = strip_tags(year_match.group(1))
            continue
        for match in re.finditer(r'<a href="#self" class="period_([^"]*)" onclick="fnViewDetail\(\'([^\']+)\'[^>]*>(.*?)</a>', chunk, re.S):
            css_type, level_id, body = match.groups()
            title_match = re.search(r'<strong class="sbj">(.*?)</strong>', body, re.S)
            time_match = re.search(r'<span class="time">(.*?)</span>', body, re.S)
            title = strip_tags(title_match.group(1)) if title_match else ""
            period = strip_tags(time_match.group(1)) if time_match else ""
            if title:
                rows.append(
                    {
                        "age_id": str(age_id),
                        "age": AGES.get(age_id, ""),
                        "year": current_year,
                        "title": title,
                        "period": period,
                        "content_type": css_type,
                        "level_id": level_id,
                        "source_url": f"{BASE_URL}/front/kc/main.do",
                    }
                )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl visible Korean History timeline list only.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    for age_id in AGES:
        rows.extend(parse_age(age_id, fetch_age(age_id, args.timeout)))
        time.sleep(args.delay)

    with args.output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["age_id", "age", "year", "title", "period", "content_type", "level_id", "source_url"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"saved={args.output} rows={len(rows)}")


if __name__ == "__main__":
    main()
