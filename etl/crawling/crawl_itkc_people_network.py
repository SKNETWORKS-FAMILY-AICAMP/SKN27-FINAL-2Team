from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "etl" / "raw_data" / "한국고전종합DB_관계망"
BASE_URL = "https://db.itkc.or.kr"
USER_AGENT = "Himate-ITKC-Network-Collector/0.1 (student project; low rate)"

DEFAULT_SEEDS = {
    "person": [
        f"{BASE_URL}/people/item?gubun=person",
    ],
    "event_subject": [
        f"{BASE_URL}/people/item?gubun=evnt",
        f"{BASE_URL}/people/treeAjax?gubun=evntcate",
    ],
    "event_period": [
        f"{BASE_URL}/people/item?gubun=evnt",
        f"{BASE_URL}/people/treeAjax?gubun=evntera",
    ],
}

RAW_RESPONSE_FIELDS = [
    "scope",
    "url",
    "status",
    "content_type",
    "encoding",
    "sha256",
    "saved_path",
    "discovered_from",
    "fetched_at",
]
NODE_FIELDS = [
    "scope",
    "node_id",
    "node_type",
    "name",
    "hanja",
    "period",
    "category",
    "description",
    "source_url",
    "raw_json",
]
EDGE_FIELDS = [
    "scope",
    "source_id",
    "source_name",
    "relation_type",
    "relation_label",
    "target_id",
    "target_name",
    "event_id",
    "event_name",
    "source_url",
    "raw_json",
]
ERROR_FIELDS = ["scope", "url", "discovered_from", "error", "failed_at"]
PEOPLE_FIELDS = [
    "person_id",
    "name",
    "birth_year",
    "death_year",
    "bonkwan",
    "ja",
    "ho",
    "father",
    "related_count",
    "detail_url",
]
PERSON_RELATION_FIELDS = [
    "person_id",
    "person_name",
    "relation_type",
    "related_person_id",
    "related_person_name",
    "related_birth_year",
    "related_death_year",
    "related_bonkwan",
    "related_father",
    "related_count",
    "evidence_url",
    "detail_url",
]
EVENT_FIELDS = [
    "scope",
    "event_id",
    "event_name",
    "subject_category",
    "period",
    "event_date",
    "person_count",
    "related_event",
    "detail_url",
]
EVENT_RELATION_FIELDS = [
    "scope",
    "event_id",
    "event_name",
    "relation_type",
    "person_id",
    "person_name",
    "related_event_id",
    "related_event_name",
    "evidence_url",
    "detail_url",
]


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def clean_text(value: str | None) -> str:
    value = html.unescape(value or "").replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def unique_values(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        value = (value or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def safe_filename(value: str, fallback: str = "response") -> str:
    value = clean_text(value)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value)
    value = re.sub(r"\s+", "_", value).strip("._")
    return (value or fallback)[:140]


def normalize_url(url: str, base: str = BASE_URL) -> str:
    joined = urllib.parse.urljoin(base, html.unescape(url))
    split = urllib.parse.urlsplit(joined)
    path = urllib.parse.quote(split.path, safe="/%")
    query = urllib.parse.quote(split.query, safe="=&%_-.,:")
    return urllib.parse.urlunsplit((split.scheme, split.netloc, path, query, ""))


def is_same_site(url: str) -> bool:
    return urllib.parse.urlparse(url).netloc == urllib.parse.urlparse(BASE_URL).netloc


def url_scope_allowed(url: str, scope: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc and not is_same_site(url):
        return False
    if "/people/" not in parsed.path and not parsed.path.endswith(".js"):
        return False

    query = urllib.parse.parse_qs(parsed.query)
    gubun = " ".join(query.get("gubun", []))
    combined = f"{parsed.path}?{parsed.query}".lower()

    if scope == "person":
        return "person" in gubun.lower() or "person" in combined or "people" in combined
    if scope == "event_subject":
        return "evntcate" in combined or "gubun=evnt" in combined or "event" in combined
    if scope == "event_period":
        return "evntera" in combined or "gubun=evnt" in combined or "event" in combined
    return False


def classify_node_type(scope: str, item: dict[str, Any]) -> str:
    text = json.dumps(item, ensure_ascii=False)
    if scope == "person":
        return "Person"
    if scope.startswith("event"):
        if any(key in item for key in ("eventId", "evntId", "event_id", "evnt_id")):
            return "Event"
        if "시기" in text or "era" in text.lower():
            return "EventPeriod"
        if "주제" in text or "cate" in text.lower():
            return "EventCategory"
        return "Event"
    return "Unknown"


def first_value(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return clean_text(str(value))
    return ""


def item_id(item: dict[str, Any]) -> str:
    return first_value(
        item,
        (
            "id",
            "ID",
            "personId",
            "person_id",
            "pid",
            "evntId",
            "eventId",
            "event_id",
            "code",
            "key",
            "value",
            "nodeId",
            "node_id",
        ),
    )


def item_name(item: dict[str, Any]) -> str:
    return first_value(
        item,
        (
            "name",
            "personName",
            "person_name",
            "korName",
            "kor_name",
            "title",
            "text",
            "label",
            "eventName",
            "event_name",
            "evntName",
            "nm",
        ),
    )


def item_hanja(item: dict[str, Any]) -> str:
    return first_value(item, ("hanja", "chName", "ch_name", "cn", "hanjaName"))


def item_period(item: dict[str, Any]) -> str:
    return first_value(item, ("period", "era", "age", "time", "year", "birthDeath", "date"))


def item_category(item: dict[str, Any]) -> str:
    return first_value(item, ("category", "cate", "cateName", "cate_name", "type", "gubun"))


def item_description(item: dict[str, Any]) -> str:
    return first_value(item, ("description", "desc", "content", "summary", "memo", "note"))


def stable_node_id(scope: str, item: dict[str, Any], source_url: str) -> str:
    explicit_id = item_id(item)
    name = item_name(item)
    if explicit_id:
        return f"itkc:{scope}:{explicit_id}"
    digest = hashlib.sha1(f"{scope}|{name}|{source_url}".encode("utf-8")).hexdigest()[:16]
    return f"itkc:{scope}:auto:{digest}"


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.scripts: list[str] = []
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a" and values.get("href"):
            self.links.append(values["href"] or "")
        if tag == "script" and values.get("src"):
            self.scripts.append(values["src"] or "")
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = clean_text(data)
        if text:
            self.parts.append(text)


@dataclass
class FetchResult:
    url: str
    status: int
    content_type: str
    encoding: str
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode(self.encoding or "utf-8", errors="replace")


class GentleClient:
    def __init__(self, delay: float, jitter: float, timeout: int, retries: int) -> None:
        self.delay = max(delay, 0.0)
        self.jitter = max(jitter, 0.0)
        self.timeout = timeout
        self.retries = retries
        self.last_request_at = 0.0
        self.opener = urllib.request.build_opener()

    def wait(self) -> None:
        elapsed = time.time() - self.last_request_at
        target = self.delay + random.uniform(0, self.jitter)
        if elapsed < target:
            time.sleep(target - elapsed)

    def fetch(self, url: str) -> FetchResult:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self.wait()
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/json,text/javascript,*/*;q=0.8",
                    "Referer": f"{BASE_URL}/people/item?gubun=person",
                },
            )
            try:
                with self.opener.open(req, timeout=self.timeout) as response:
                    body = response.read()
                    self.last_request_at = time.time()
                    content_type = response.headers.get("Content-Type", "")
                    encoding = response.headers.get_content_charset() or "utf-8"
                    return FetchResult(url, response.status, content_type, encoding, body)
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                self.last_request_at = time.time()
                if attempt < self.retries:
                    time.sleep(min(30.0, (attempt + 1) * self.delay + random.uniform(0, self.jitter)))
        raise RuntimeError(f"request failed: {last_error}")


class CsvWriter:
    def __init__(self, path: Path, fields: list[str], append: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not append or not path.exists() or path.stat().st_size == 0
        self.file = path.open("a" if append else "w", encoding="utf-8-sig", newline="")
        self.writer = csv.DictWriter(self.file, fieldnames=fields)
        if write_header:
            self.writer.writeheader()

    def write(self, row: dict[str, Any]) -> None:
        self.writer.writerow(row)
        self.file.flush()

    def close(self) -> None:
        self.file.close()


@dataclass
class ResumeState:
    people: set[str]
    person_relations: set[tuple[str, str, str]]
    events: set[tuple[str, str]]
    event_relations: set[tuple[str, str, str, str]]
    fetched_urls: set[str]


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def load_resume_state(output_dir: Path) -> ResumeState:
    people = {
        row.get("person_id", "")
        for row in read_csv_rows(output_dir / "itkc_people.csv")
        if row.get("person_id")
    }
    person_relations = {
        (
            row.get("person_id", ""),
            row.get("relation_type", ""),
            row.get("related_person_id", "") or row.get("related_person_name", ""),
        )
        for row in read_csv_rows(output_dir / "itkc_person_relations.csv")
        if row.get("person_id")
    }
    events = {
        (row.get("scope", ""), row.get("event_id", ""))
        for row in read_csv_rows(output_dir / "itkc_events.csv")
        if row.get("event_id")
    }
    event_relations = {
        (
            row.get("scope", ""),
            row.get("event_id", ""),
            row.get("relation_type", ""),
            row.get("person_id", "") or row.get("related_event_id", "") or row.get("person_name", ""),
        )
        for row in read_csv_rows(output_dir / "itkc_event_relations.csv")
        if row.get("event_id")
    }
    fetched_urls = {
        normalize_url(row.get("url", ""))
        for row in read_csv_rows(output_dir / "itkc_raw_responses.csv")
        if row.get("url")
    }
    return ResumeState(
        people=people,
        person_relations=person_relations,
        events=events,
        event_relations=event_relations,
        fetched_urls=fetched_urls,
    )


def discover_urls(text: str, base_url: str) -> list[str]:
    urls: list[str] = []
    parser = LinkExtractor()
    if "<" in text:
        try:
            parser.feed(text)
            urls.extend(parser.links)
            urls.extend(parser.scripts)
        except Exception:
            pass

    patterns = [
        r"""["']([^"']*/people/[^"']+)["']""",
        r"""["']([^"']*(?:treeAjax|listAjax|detailAjax|searchAjax|relationAjax)[^"']*)["']""",
        r"""url\s*:\s*["']([^"']+)["']""",
        r"""href\s*=\s*["']([^"']+)["']""",
    ]
    for pattern in patterns:
        urls.extend(re.findall(pattern, text, flags=re.IGNORECASE))

    normalized: list[str] = []
    for url in urls:
        if not url or url.startswith(("javascript:", "#", "mailto:")):
            continue
        normalized_url = normalize_url(url, base_url)
        if is_same_site(normalized_url) and normalized_url not in normalized:
            normalized.append(normalized_url)
    return normalized


def parse_json_maybe(text: str) -> Any | None:
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def iter_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(iter_dicts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(iter_dicts(child))
    return found


def node_from_json(scope: str, item: dict[str, Any], source_url: str) -> dict[str, Any] | None:
    name = item_name(item)
    if not name:
        return None
    return {
        "scope": scope,
        "node_id": stable_node_id(scope, item, source_url),
        "node_type": classify_node_type(scope, item),
        "name": name,
        "hanja": item_hanja(item),
        "period": item_period(item),
        "category": item_category(item),
        "description": item_description(item),
        "source_url": source_url,
        "raw_json": json.dumps(item, ensure_ascii=False),
    }


def edges_from_json(scope: str, item: dict[str, Any], source_url: str) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    source_name = first_value(item, ("sourceName", "fromName", "personName", "name"))
    target_name = first_value(item, ("targetName", "toName", "relName", "relationName", "otherName"))
    relation_label = first_value(item, ("relation", "relationType", "relationLabel", "relType", "rel"))
    event_name = first_value(item, ("eventName", "evntName", "title"))

    if source_name and target_name:
        source_id = first_value(item, ("sourceId", "fromId", "personId", "pid"))
        target_id = first_value(item, ("targetId", "toId", "relId", "otherId"))
        edges.append(
            {
                "scope": scope,
                "source_id": f"itkc:person:{source_id}" if source_id else "",
                "source_name": source_name,
                "relation_type": "RELATED_TO",
                "relation_label": relation_label,
                "target_id": f"itkc:person:{target_id}" if target_id else "",
                "target_name": target_name,
                "event_id": first_value(item, ("eventId", "evntId")),
                "event_name": event_name,
                "source_url": source_url,
                "raw_json": json.dumps(item, ensure_ascii=False),
            }
        )
    return edges


def strip_tags(value: str) -> str:
    value = re.sub(r"(?is)<script.*?</script>", " ", value)
    value = re.sub(r"(?is)<style.*?</style>", " ", value)
    value = re.sub(r"(?s)<br\s*/?>", "\n", value)
    value = re.sub(r"(?s)<.*?>", " ", value)
    return clean_text(value)


def table_rows(fragment: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", fragment):
        cells = re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", tr)
        if cells:
            rows.append([strip_tags(cell) for cell in cells])
    return rows


def data_id_from_href(fragment: str) -> str:
    match = re.search(r"dataId=([A-Za-z0-9_:-]+)", fragment)
    return match.group(1) if match else ""


def view_url(gubun: str, data_id: str, cate1: str = "Z", cate2: str = "") -> str:
    params = urllib.parse.urlencode({"gubun": gubun, "cate1": cate1, "cate2": cate2, "dataId": data_id})
    path = "viewEvnt" if gubun.startswith("evnt") else "view"
    return f"{BASE_URL}/people/{path}?{params}"


def parse_person_list(html_text: str, list_url: str) -> list[dict[str, str]]:
    people: list[dict[str, str]] = []
    for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", html_text):
        if "dataId=P" not in tr:
            continue
        cells = re.findall(r"(?is)<td[^>]*>(.*?)</td>", tr)
        if len(cells) < 9:
            continue
        person_id = data_id_from_href(cells[1])
        name = strip_tags(cells[1])
        if not person_id or not name:
            continue
        people.append(
            {
                "person_id": person_id,
                "name": name,
                "birth_year": strip_tags(cells[2]),
                "death_year": strip_tags(cells[3]),
                "bonkwan": strip_tags(cells[4]),
                "ja": strip_tags(cells[5]),
                "ho": strip_tags(cells[6]),
                "father": strip_tags(cells[7]),
                "related_count": strip_tags(cells[8]),
                "detail_url": view_url("person", person_id),
            }
        )
    return people


def parse_person_relations(html_text: str, person: dict[str, str], detail_url: str) -> list[dict[str, str]]:
    marker = html_text.find("관계인물")
    if marker == -1:
        return []
    relation_html = html_text[marker:]
    relations: list[dict[str, str]] = []
    for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", relation_html):
        if "dataId=P" not in tr:
            continue
        cells = re.findall(r"(?is)<td[^>]*>(.*?)</td>", tr)
        if len(cells) < 8:
            continue
        related_id = data_id_from_href(cells[1])
        evidence_match = re.search(r'(?is)<a[^>]+href=["\']([^"\']+)["\'][^>]*>\s*바로가기\s*</a>', tr)
        evidence_url = normalize_url(evidence_match.group(1), detail_url) if evidence_match else ""
        relations.append(
            {
                "person_id": person["person_id"],
                "person_name": person["name"],
                "relation_type": strip_tags(cells[2]),
                "related_person_id": related_id,
                "related_person_name": strip_tags(cells[1]),
                "related_birth_year": strip_tags(cells[3]),
                "related_death_year": strip_tags(cells[4]),
                "related_bonkwan": strip_tags(cells[5]),
                "related_father": strip_tags(cells[6]),
                "related_count": strip_tags(cells[7]),
                "evidence_url": evidence_url,
                "detail_url": detail_url,
            }
        )
    return relations


def parse_event_list(html_text: str, scope: str) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", html_text):
        if "dataId=ITKC" not in tr:
            continue
        cells = re.findall(r"(?is)<td[^>]*>(.*?)</td>", tr)
        if len(cells) < 7:
            continue
        event_id = data_id_from_href(cells[1])
        event_name = strip_tags(cells[1])
        if not event_id or not event_name:
            continue
        gubun = "evntcate" if scope == "event_subject" else "evntera"
        events.append(
            {
                "scope": scope,
                "event_id": event_id,
                "event_name": event_name,
                "subject_category": strip_tags(cells[2]),
                "period": strip_tags(cells[3]),
                "event_date": strip_tags(cells[4]),
                "person_count": strip_tags(cells[5]),
                "related_event": strip_tags(cells[6]),
                "detail_url": view_url(gubun, event_id),
            }
        )
    return events


def parse_event_relations(html_text: str, event: dict[str, str], detail_url: str) -> list[dict[str, str]]:
    relations: list[dict[str, str]] = []
    for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", html_text):
        if "dataId=P" not in tr and "dataId=ITKC" not in tr:
            continue
        cells = re.findall(r"(?is)<td[^>]*>(.*?)</td>", tr)
        if len(cells) < 2:
            continue
        person_id = data_id_from_href(tr) if "dataId=P" in tr else ""
        related_event_id = data_id_from_href(tr) if "dataId=ITKC" in tr else ""
        name_cell = strip_tags(cells[1]) if len(cells) > 1 else ""
        relation_type = "사건인물" if person_id else "관련사건"
        evidence_match = re.search(r'(?is)<a[^>]+href=["\']([^"\']+)["\'][^>]*>\s*바로가기\s*</a>', tr)
        evidence_url = normalize_url(evidence_match.group(1), detail_url) if evidence_match else ""
        relations.append(
            {
                "scope": event["scope"],
                "event_id": event["event_id"],
                "event_name": event["event_name"],
                "relation_type": relation_type,
                "person_id": person_id,
                "person_name": name_cell if person_id else "",
                "related_event_id": related_event_id,
                "related_event_name": name_cell if related_event_id else "",
                "evidence_url": evidence_url,
                "detail_url": detail_url,
            }
        )
    return relations


def tree_category_urls(scope: str) -> list[str]:
    if scope == "person":
        return [f"{BASE_URL}/people/list?gubun=person&cate1=Z"]
    if scope == "event_period":
        return [
            f"{BASE_URL}/people/list?gubun=evntera&depth=1&cate1={urllib.parse.quote(period)}"
            for period in ("고려", "조선", "삼국", "통일신라", "근대", "현대")
        ]
    gubun = "evntcate" if scope == "event_subject" else "evntera"
    return [
        f"{BASE_URL}/people/treeAjax?gubun={gubun}",
        f"{BASE_URL}/people/list?gubun={gubun}&depth=1&cate1=Z",
    ]


def discover_category_list_urls(scope: str, html_text: str) -> list[str]:
    urls: list[str] = []
    for raw in re.findall(r"\{url:([^}]+)\}", html_text):
        raw = raw.strip().strip("'\"")
        if "treeAjax" in raw:
            urls.append(normalize_url(raw))
    for raw in re.findall(r"data-url\s*=\s*['\"]([^'\"]+)['\"]", html_text):
        if "gubun=" in raw:
            urls.append(normalize_url(f"/people/list{raw}"))
    if scope == "person":
        for cate in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            urls.append(f"{BASE_URL}/people/list?gubun=person&cate1={cate}")
    return unique_values(urls)


def paged_url(url: str, page_index: int, page_unit: int) -> str:
    split = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(split.query)
    query["pageIndex"] = [str(page_index)]
    query["pageUnit"] = [str(page_unit)]
    encoded = urllib.parse.urlencode(query, doseq=True)
    return urllib.parse.urlunsplit((split.scheme, split.netloc, split.path, encoded, ""))


def crawl_direct_scope(
    scope: str,
    output_dir: Path,
    client: GentleClient,
    raw_writer: CsvWriter,
    people_writer: CsvWriter,
    person_relation_writer: CsvWriter,
    event_writer: CsvWriter,
    event_relation_writer: CsvWriter,
    error_writer: CsvWriter,
    max_pages: int,
    max_items: int,
    page_unit: int,
    save_raw: bool,
    resume_state: ResumeState,
) -> None:
    fetched_items = 0
    category_urls: list[str] = []
    for seed in tree_category_urls(scope):
        try:
            result = client.fetch(seed)
            write_response_log(raw_writer, resume_state, result, output_dir, scope, "", save_raw)
            category_urls.extend(discover_category_list_urls(scope, result.text))
        except Exception as exc:
            error_writer.write({"scope": scope, "url": seed, "discovered_from": "", "error": repr(exc), "failed_at": now_text()})

    category_urls = unique_values(category_urls) or tree_category_urls(scope)
    for category_url in category_urls:
        for page_index in range(1, max_pages + 1):
            if fetched_items >= max_items:
                return
            list_url = paged_url(category_url.replace("/treeAjax", "/list"), page_index, page_unit)
            try:
                result = client.fetch(list_url)
                write_response_log(raw_writer, resume_state, result, output_dir, scope, category_url, save_raw)
            except Exception as exc:
                error_writer.write(
                    {"scope": scope, "url": list_url, "discovered_from": category_url, "error": repr(exc), "failed_at": now_text()}
                )
                continue

            if scope == "person":
                people = parse_person_list(result.text, result.url)
                if not people:
                    break
                for person in people:
                    if fetched_items >= max_items:
                        return
                    already_written = person["person_id"] in resume_state.people
                    if not already_written:
                        people_writer.write(person)
                        resume_state.people.add(person["person_id"])
                        fetched_items += 1
                    detail_already_fetched = normalize_url(person["detail_url"]) in resume_state.fetched_urls
                    if already_written and detail_already_fetched:
                        continue
                    try:
                        detail = client.fetch(person["detail_url"])
                        write_response_log(raw_writer, resume_state, detail, output_dir, scope, result.url, save_raw)
                        for relation in parse_person_relations(detail.text, person, detail.url):
                            relation_key = (
                                relation["person_id"],
                                relation["relation_type"],
                                relation["related_person_id"] or relation["related_person_name"],
                            )
                            if relation_key in resume_state.person_relations:
                                continue
                            person_relation_writer.write(relation)
                            resume_state.person_relations.add(relation_key)
                    except Exception as exc:
                        error_writer.write(
                            {
                                "scope": scope,
                                "url": person["detail_url"],
                                "discovered_from": result.url,
                                "error": repr(exc),
                                "failed_at": now_text(),
                            }
                        )
            else:
                events = parse_event_list(result.text, scope)
                if not events:
                    break
                for event in events:
                    if fetched_items >= max_items:
                        return
                    event_key = (event["scope"], event["event_id"])
                    already_written = event_key in resume_state.events
                    if not already_written:
                        event_writer.write(event)
                        resume_state.events.add(event_key)
                        fetched_items += 1
                    detail_already_fetched = normalize_url(event["detail_url"]) in resume_state.fetched_urls
                    if already_written and detail_already_fetched:
                        continue
                    try:
                        detail = client.fetch(event["detail_url"])
                        write_response_log(raw_writer, resume_state, detail, output_dir, scope, result.url, save_raw)
                        for relation in parse_event_relations(detail.text, event, detail.url):
                            relation_key = (
                                relation["scope"],
                                relation["event_id"],
                                relation["relation_type"],
                                relation["person_id"] or relation["related_event_id"] or relation["person_name"],
                            )
                            if relation_key in resume_state.event_relations:
                                continue
                            event_relation_writer.write(relation)
                            resume_state.event_relations.add(relation_key)
                    except Exception as exc:
                        error_writer.write(
                            {
                                "scope": scope,
                                "url": event["detail_url"],
                                "discovered_from": result.url,
                                "error": repr(exc),
                                "failed_at": now_text(),
                            }
                        )


def html_node(scope: str, result: FetchResult) -> dict[str, Any] | None:
    if "text/html" not in result.content_type.lower():
        return None
    title_parser = LinkExtractor()
    text_parser = TextExtractor()
    try:
        title_parser.feed(result.text)
        text_parser.feed(result.text)
    except Exception:
        return None
    title = clean_text(" ".join(title_parser.title_parts))
    if not title:
        return None
    return {
        "scope": scope,
        "node_id": f"itkc:{scope}:page:{hashlib.sha1(result.url.encode('utf-8')).hexdigest()[:16]}",
        "node_type": "Page",
        "name": title,
        "hanja": "",
        "period": "",
        "category": "",
        "description": clean_text("\n".join(text_parser.parts))[:500],
        "source_url": result.url,
        "raw_json": "",
    }


def save_response(result: FetchResult, output_dir: Path, scope: str, save_raw: bool) -> tuple[str, str]:
    digest = hashlib.sha256(result.body).hexdigest()
    if not save_raw:
        return digest, ""
    suffix = ".json" if "json" in result.content_type.lower() else ".html"
    raw_dir = output_dir / "raw_responses" / scope
    raw_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_filename(urllib.parse.urlparse(result.url).path, 'response')}_{digest[:12]}{suffix}"
    path = raw_dir / filename
    path.write_bytes(result.body)
    return digest, str(path.relative_to(output_dir))


def write_response_log(
    writer: CsvWriter,
    state: ResumeState,
    result: FetchResult,
    output_dir: Path,
    scope: str,
    discovered_from: str,
    save_raw: bool,
) -> None:
    normalized_url = normalize_url(result.url)
    if normalized_url in state.fetched_urls:
        return
    digest, saved_path = save_response(result, output_dir, scope, save_raw)
    state.fetched_urls.add(normalized_url)
    writer.write(
        {
            "scope": scope,
            "url": result.url,
            "status": result.status,
            "content_type": result.content_type,
            "encoding": result.encoding,
            "sha256": digest,
            "saved_path": saved_path,
            "discovered_from": discovered_from,
            "fetched_at": now_text(),
        }
    )


def crawl_scope(
    scope: str,
    output_dir: Path,
    client: GentleClient,
    raw_writer: CsvWriter,
    node_writer: CsvWriter,
    edge_writer: CsvWriter,
    error_writer: CsvWriter,
    max_pages: int,
    max_depth: int,
    max_queue: int,
) -> None:
    queue: list[tuple[str, str, int]] = [(url, "", 0) for url in DEFAULT_SEEDS[scope]]
    seen: set[str] = set()
    written_nodes: set[str] = set()
    fetched = 0

    while queue and fetched < max_pages:
        url, discovered_from, depth = queue.pop(0)
        url = normalize_url(url)
        if url in seen:
            continue
        seen.add(url)
        if not url_scope_allowed(url, scope):
            continue

        try:
            result = client.fetch(url)
            fetched += 1
            digest, saved_path = save_response(result, output_dir, scope, True)
            raw_writer.write(
                {
                    "scope": scope,
                    "url": result.url,
                    "status": result.status,
                    "content_type": result.content_type,
                    "encoding": result.encoding,
                    "sha256": digest,
                    "saved_path": saved_path,
                    "discovered_from": discovered_from,
                    "fetched_at": now_text(),
                }
            )
        except Exception as exc:
            error_writer.write(
                {
                    "scope": scope,
                    "url": url,
                    "discovered_from": discovered_from,
                    "error": repr(exc),
                    "failed_at": now_text(),
                }
            )
            continue

        parsed_json = parse_json_maybe(result.text)
        if parsed_json is not None:
            for item in iter_dicts(parsed_json):
                node = node_from_json(scope, item, result.url)
                if node and node["node_id"] not in written_nodes:
                    node_writer.write(node)
                    written_nodes.add(node["node_id"])
                for edge in edges_from_json(scope, item, result.url):
                    edge_writer.write(edge)
        else:
            node = html_node(scope, result)
            if node and node["node_id"] not in written_nodes:
                node_writer.write(node)
                written_nodes.add(node["node_id"])

        if depth >= max_depth:
            continue
        for next_url in discover_urls(result.text, result.url):
            if len(queue) >= max_queue:
                break
            if next_url not in seen and url_scope_allowed(next_url, scope):
                queue.append((next_url, result.url, depth + 1))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Slowly crawl ITKC people/event network pages and save relationship CSV files."
    )
    parser.add_argument(
        "--scope",
        choices=["person", "event_subject", "event_period", "all"],
        default="all",
        help="Crawl person, event subject, event period, or all supported scopes.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between requests.")
    parser.add_argument("--jitter", type=float, default=1.0, help="Random extra delay seconds.")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--max-pages", type=int, default=30, help="Max fetched pages per scope.")
    parser.add_argument("--max-items", type=int, default=100, help="Max people/events with detail pages per scope.")
    parser.add_argument("--page-unit", type=int, default=20, help="List page size. Keep small for gentle crawling.")
    parser.add_argument("--max-depth", type=int, default=2, help="Discovery depth from seed pages.")
    parser.add_argument("--max-queue", type=int, default=500, help="Max pending URLs per scope.")
    parser.add_argument(
        "--save-raw",
        action="store_true",
        help="Also save raw HTML/JSON responses under raw_responses/. By default only CSV metadata is written.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Append to existing CSV files and skip already collected people/events/relations.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    scopes = ["person", "event_subject", "event_period"] if args.scope == "all" else [args.scope]
    client = GentleClient(delay=args.delay, jitter=args.jitter, timeout=args.timeout, retries=args.retries)
    resume_state = load_resume_state(output_dir) if args.resume else ResumeState(set(), set(), set(), set(), set())

    raw_writer = CsvWriter(output_dir / "itkc_raw_responses.csv", RAW_RESPONSE_FIELDS, append=args.resume)
    people_writer = CsvWriter(output_dir / "itkc_people.csv", PEOPLE_FIELDS, append=args.resume)
    person_relation_writer = CsvWriter(output_dir / "itkc_person_relations.csv", PERSON_RELATION_FIELDS, append=args.resume)
    event_writer = CsvWriter(output_dir / "itkc_events.csv", EVENT_FIELDS, append=args.resume)
    event_relation_writer = CsvWriter(output_dir / "itkc_event_relations.csv", EVENT_RELATION_FIELDS, append=args.resume)
    error_writer = CsvWriter(output_dir / "itkc_errors.csv", ERROR_FIELDS, append=args.resume)
    try:
        for scope in scopes:
            crawl_direct_scope(
                scope=scope,
                output_dir=output_dir,
                client=client,
                raw_writer=raw_writer,
                people_writer=people_writer,
                person_relation_writer=person_relation_writer,
                event_writer=event_writer,
                event_relation_writer=event_relation_writer,
                error_writer=error_writer,
                max_pages=args.max_pages,
                max_items=args.max_items,
                page_unit=args.page_unit,
                save_raw=args.save_raw,
                resume_state=resume_state,
            )
    finally:
        raw_writer.close()
        people_writer.close()
        person_relation_writer.close()
        event_writer.close()
        event_relation_writer.close()
        error_writer.close()

    print(f"Saved CSV files to: {output_dir}")
    print("Generated CSV files only. No database load is performed.")
    if args.resume:
        print("Resume mode enabled: existing CSV rows were loaded and duplicates were skipped.")
    if args.save_raw:
        print("Raw HTML/JSON responses were also saved under raw_responses/.")
    else:
        print("Raw HTML/JSON files were not saved. Use --save-raw only when debugging or re-parsing.")
    print(
        "Files: itkc_people.csv, itkc_person_relations.csv, "
        "itkc_events.csv, itkc_event_relations.csv, itkc_raw_responses.csv, itkc_errors.csv"
    )


if __name__ == "__main__":
    main()
