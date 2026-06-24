from __future__ import annotations

import argparse
import csv
import html
import json
import os
import random
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "etl" / "raw_data"
BASE_URL = "https://contents.history.go.kr"
MAIN_URL = f"{BASE_URL}/front/hm/main.do"
USER_AGENT = "HistoryContents-HM-Collector/1.0 (personal research; low rate)"

PERIODS = [
    ("hm_age_10", "삼국 이전"),
    ("hm_age_20", "삼국 시대"),
    ("hm_age_30", "통일 신라와 발해"),
    ("hm_age_40", "고려 시대"),
    ("hm_age_50", "조선 전기"),
    ("hm_age_60", "조선 후기"),
    ("hm_age_70", "근대"),
    ("hm_age_80", "현대"),
]

TYPES = [
    ("hm_ty_010", "정치"),
    ("hm_ty_020", "경제"),
    ("hm_ty_030", "사회"),
    ("hm_ty_040", "문화"),
]

CSV_FIELDS = [
    "순번",
    "시대코드",
    "시대",
    "분야코드",
    "분야",
    "자료ID",
    "제목",
    "목차경로",
    "국문",
    "원문",
    "해설",
    "참고자료",
    "상세URL",
    "Markdown파일",
]

FAILED_FIELDS = ["종류", "시대코드", "시대", "분야코드", "분야", "자료ID", "URL", "오류"]


def clean_text(value: str) -> str:
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def safe_filename(value: str, fallback: str = "untitled") -> str:
    value = clean_text(value)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or fallback)[:120]


def normalize_url(href: str, base: str = BASE_URL) -> str:
    url = urllib.parse.urljoin(base, html.unescape(href))
    split = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(split.path, safe="/%")
    query = urllib.parse.quote(split.query, safe="=&%_")
    return urllib.parse.urlunsplit((split.scheme, split.netloc, path, query, split.fragment))


def get_level_id(url: str) -> str:
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return (query.get("levelId") or [""])[0]


@dataclass(frozen=True)
class DetailLink:
    level_id: str
    url: str
    period_code: str
    period_name: str
    type_code: str
    type_name: str
    order: int


@dataclass
class DetailPage:
    level_id: str
    title: str
    breadcrumb: str
    panels: dict[str, str]
    url: str


class GentleClient:
    def __init__(self, delay: float, jitter: float, timeout: int, retries: int) -> None:
        self.delay = max(0.0, delay)
        self.jitter = max(0.0, jitter)
        self.timeout = timeout
        self.retries = retries
        self.last_request_at = 0.0
        self.opener = urllib.request.build_opener()

    def wait(self) -> None:
        target = self.delay + random.uniform(0, self.jitter)
        elapsed = time.monotonic() - self.last_request_at
        if elapsed < target:
            time.sleep(target - elapsed)

    def get_text(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                self.wait()
                request = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.5,en;q=0.3",
                        "Referer": MAIN_URL,
                    },
                )
                with self.opener.open(request, timeout=self.timeout) as response:
                    data = response.read()
                    charset = response.headers.get_content_charset() or "utf-8"
                self.last_request_at = time.monotonic()
                return data.decode(charset, errors="replace")
            except urllib.error.HTTPError as exc:
                self.last_request_at = time.monotonic()
                last_error = exc
                if exc.code not in {429, 500, 502, 503, 504}:
                    break
                wait = min(120.0, 10.0 * attempt)
                print(f"서버 응답 {exc.code}: {wait:.0f}초 후 재시도 - {url}", flush=True)
                time.sleep(wait)
            except (urllib.error.URLError, TimeoutError) as exc:
                self.last_request_at = time.monotonic()
                last_error = exc
                wait = min(90.0, 5.0 * attempt)
                print(f"요청 실패 {attempt}/{self.retries}: {wait:.0f}초 후 재시도 - {url} -> {exc}", flush=True)
                time.sleep(wait)
        raise RuntimeError(f"요청 실패: {url}") from last_error


class DetailHTMLParser(HTMLParser):
    PANEL_MAP = {
        "panel_국문": "국문",
        "panel_원문": "원문",
        "panel_해설": "해설",
        "panel_참고자료": "참고자료",
    }
    BLOCK_TAGS = {"br", "p", "div", "li", "tr", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_depth: int | None = None
        self.breadcrumb_depth: int | None = None
        self.article_depth: int | None = None
        self.current_panel: str | None = None
        self.depth = 0
        self.skip_until_depth: int | None = None
        self.title_parts: list[str] = []
        self.breadcrumb_parts: list[str] = []
        self.panel_parts: dict[str, list[str]] = {name: [] for name in self.PANEL_MAP.values()}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.depth += 1
        attr = dict(attrs)
        classes = set((attr.get("class") or "").split())

        if self.skip_until_depth is not None:
            return
        if (
            tag in {"script", "style", "noscript", "select", "button"}
            or "layerRef_wrap" in classes
            or "toolbar" in classes
            or "UTIL" in classes
        ):
            self.skip_until_depth = self.depth
            return

        if tag == "h2" and self.title_depth is None:
            self.title_depth = self.depth
        if tag == "ol" and "breadcrumb" in classes and self.breadcrumb_depth is None:
            self.breadcrumb_depth = self.depth

        if tag == "article" and self.current_panel is None:
            for class_name, panel_name in self.PANEL_MAP.items():
                if class_name in classes:
                    self.current_panel = panel_name
                    self.article_depth = self.depth
                    break

        if tag == "a" and self.current_panel and attr.get("href"):
            href = attr["href"]
            if href and not href.startswith("javascript:"):
                self.panel_parts[self.current_panel].append(f" [{normalize_url(href)}] ")

        if tag in self.BLOCK_TAGS:
            self._append_newline()

    def handle_endtag(self, tag: str) -> None:
        if self.skip_until_depth is not None:
            if self.depth == self.skip_until_depth:
                self.skip_until_depth = None
            self.depth -= 1
            return

        if tag in self.BLOCK_TAGS:
            self._append_newline()

        if self.title_depth == self.depth:
            self.title_depth = None
        if self.breadcrumb_depth == self.depth:
            self.breadcrumb_depth = None
        if self.article_depth == self.depth:
            self.article_depth = None
            self.current_panel = None
        self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_until_depth is not None:
            return
        if self.title_depth is not None:
            self.title_parts.append(data)
        if self.breadcrumb_depth is not None:
            self.breadcrumb_parts.append(data)
        if self.current_panel:
            self.panel_parts[self.current_panel].append(data)

    def _append_newline(self) -> None:
        if self.title_depth is not None:
            self.title_parts.append("\n")
        if self.breadcrumb_depth is not None:
            self.breadcrumb_parts.append("\n")
        if self.current_panel:
            self.panel_parts[self.current_panel].append("\n")

    def result(self) -> tuple[str, str, dict[str, str]]:
        title = clean_text("".join(self.title_parts))
        breadcrumb_lines = [line for line in clean_text("".join(self.breadcrumb_parts)).splitlines() if line]
        breadcrumb = " > ".join(dict.fromkeys(breadcrumb_lines))
        panels = {name: clean_text("".join(parts)) for name, parts in self.panel_parts.items()}
        return title, breadcrumb, panels


def discover_list_urls() -> list[tuple[str, str, str, str, str]]:
    urls: list[tuple[str, str, str, str, str]] = []
    for period_code, period_name in PERIODS:
        for type_code, type_name in TYPES:
            params = urllib.parse.urlencode({"periodCode": period_code, "typeCode": type_code})
            urls.append((f"{BASE_URL}/front/hm/list.do?{params}", period_code, period_name, type_code, type_name))
    return urls


def parse_detail_links(list_html: str, list_url: str, period_code: str, period_name: str, type_code: str, type_name: str) -> list[DetailLink]:
    links: list[DetailLink] = []
    seen: set[str] = set()
    for match in re.finditer(r'data-href=["\']([^"\']*/front/hm/view\.do\?levelId=[^"\']+)["\']', list_html):
        url = normalize_url(match.group(1), list_url)
        level_id = get_level_id(url)
        if level_id and level_id not in seen:
            seen.add(level_id)
            links.append(
                DetailLink(
                    level_id=level_id,
                    url=url,
                    period_code=period_code,
                    period_name=period_name,
                    type_code=type_code,
                    type_name=type_name,
                    order=len(links) + 1,
                )
            )
    return links


def parse_detail_page(page_html: str, url: str) -> DetailPage:
    parser = DetailHTMLParser()
    parser.feed(page_html)
    title, breadcrumb, panels = parser.result()
    level_id = get_level_id(url)
    if not title:
        title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", page_html)
        title = clean_text(re.sub(r"<[^>]+>", " ", title_match.group(1))) if title_match else level_id
        title = title.replace("< 사료로 본 한국사", "").strip()
    return DetailPage(level_id=level_id, title=title, breadcrumb=breadcrumb, panels=panels, url=url)


def load_completed_ids(index_path: Path) -> set[str]:
    if not index_path.exists():
        return set()
    with index_path.open("r", encoding="utf-8-sig", newline="") as file:
        return {row.get("자료ID", "") for row in csv.DictReader(file) if row.get("자료ID")}


def append_csv(path: Path, row: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
        file.flush()
        os.fsync(file.fileno())


def append_failed(path: Path, row: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FAILED_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
        file.flush()
        os.fsync(file.fileno())


def write_markdown(path: Path, detail: DetailPage, row: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = [
        f"# {detail.title}",
        "",
        f"- 자료ID: `{detail.level_id}`",
        f"- 시대: {row['시대']}",
        f"- 분야: {row['분야']}",
        f"- 목차경로: {detail.breadcrumb}",
        f"- 상세URL: {detail.url}",
        "",
    ]
    for name in ["국문", "원문", "해설", "참고자료"]:
        content.extend([f"## {name}", "", detail.panels.get(name, "") or "", ""])
    path.write_text("\n".join(content).rstrip() + "\n", encoding="utf-8")


def collect(args: argparse.Namespace) -> None:
    output = Path(args.output)
    index_path = output / "index.csv"
    failed_path = output / "failed_requests.csv"
    completed = load_completed_ids(index_path)
    client = GentleClient(args.delay, args.jitter, args.timeout, args.retries)

    print(f"이미 완료된 자료: {len(completed):,}건", flush=True)
    saved = 0
    skipped = 0
    failed = 0
    sequence = len(completed)

    for list_url, period_code, period_name, type_code, type_name in discover_list_urls():
        if args.period and args.period != period_code:
            continue
        if args.type and args.type != type_code:
            continue

        print(f"\n목록 확인: {period_name} / {type_name}", flush=True)
        try:
            list_html = client.get_text(list_url)
        except Exception as exc:
            failed += 1
            append_failed(
                failed_path,
                {
                    "종류": "목록",
                    "시대코드": period_code,
                    "시대": period_name,
                    "분야코드": type_code,
                    "분야": type_name,
                    "자료ID": "",
                    "URL": list_url,
                    "오류": str(exc),
                },
            )
            print(f"목록 실패, 다음 목록으로 넘어갑니다: {period_name} / {type_name} -> {exc}", flush=True)
            continue
        detail_links = parse_detail_links(list_html, list_url, period_code, period_name, type_code, type_name)
        print(f"상세 링크 {len(detail_links):,}개", flush=True)

        category_csv = output / "csv" / f"{period_code}_{safe_filename(period_name)}_{type_code}_{safe_filename(type_name)}.csv"
        for detail_link in detail_links:
            if args.limit and saved >= args.limit:
                print(f"테스트 제한 {args.limit:,}건 도달", flush=True)
                print(f"완료: 저장 {saved:,}건, 건너뜀 {skipped:,}건, 실패 {failed:,}건", flush=True)
                return
            if detail_link.level_id in completed:
                skipped += 1
                continue

            try:
                page_html = client.get_text(detail_link.url)
                detail = parse_detail_page(page_html, detail_link.url)
                if not any(detail.panels.values()):
                    raise RuntimeError("탭 본문을 찾지 못했습니다.")

                sequence += 1
                md_dir = output / "items" / f"{period_code}_{safe_filename(period_name)}" / f"{type_code}_{safe_filename(type_name)}"
                md_path = md_dir / f"{detail.level_id}_{safe_filename(detail.title, detail.level_id)}.md"
                row = {
                    "순번": str(sequence),
                    "시대코드": period_code,
                    "시대": period_name,
                    "분야코드": type_code,
                    "분야": type_name,
                    "자료ID": detail.level_id,
                    "제목": detail.title,
                    "목차경로": detail.breadcrumb,
                    "국문": detail.panels.get("국문", ""),
                    "원문": detail.panels.get("원문", ""),
                    "해설": detail.panels.get("해설", ""),
                    "참고자료": detail.panels.get("참고자료", ""),
                    "상세URL": detail.url,
                    "Markdown파일": str(md_path),
                }
                write_markdown(md_path, detail, row)
                append_csv(index_path, row)
                append_csv(category_csv, row)
                completed.add(detail.level_id)
                saved += 1
                print(f"[{sequence}] 저장 {detail.level_id} - {detail.title}", flush=True)
            except Exception as exc:
                failed += 1
                append_failed(
                    failed_path,
                    {
                        "종류": "상세",
                        "시대코드": period_code,
                        "시대": period_name,
                        "분야코드": type_code,
                        "분야": type_name,
                        "자료ID": detail_link.level_id,
                        "URL": detail_link.url,
                        "오류": str(exc),
                    },
                )
                print(f"실패 {detail_link.level_id}: {exc}", flush=True)

    print(f"\n완료: 저장 {saved:,}건, 건너뜀 {skipped:,}건, 실패 {failed:,}건 -> {output.resolve()}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="우리역사넷 사료로 본 한국사 전체 자료를 CSV와 Markdown으로 천천히 수집합니다.")
    parser.add_argument("--output", default=str(RAW_DATA_DIR / "사료로 본 한국사"), help="저장 폴더")
    parser.add_argument("--delay", type=float, default=5.0, help="요청 사이 기본 대기 초")
    parser.add_argument("--jitter", type=float, default=2.0, help="추가 랜덤 대기 초")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--limit", type=int, default=0, help="테스트용 저장 개수 제한")
    parser.add_argument("--period", default="", help="특정 시대코드만 수집 예: hm_age_10")
    parser.add_argument("--type", default="", help="특정 분야코드만 수집 예: hm_ty_010")
    args = parser.parse_args()

    if args.delay < 1.0:
        parser.error("서버 보호를 위해 --delay는 1초 이상으로 설정해 주십시오.")
    socket.setdefaulttimeout(args.timeout)
    collect(args)


if __name__ == "__main__":
    main()
