from __future__ import annotations

import argparse
import csv
import html
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "etl" / "raw_data"
BASE_URL = "https://contents.history.go.kr"
MAIN_URL = f"{BASE_URL}/front/ki/main.do"
LIST_URL = f"{BASE_URL}/front/ki/mainMoreAjax.do"
DETAIL_URL = f"{BASE_URL}/front/ki/viewAjax.do"
USER_AGENT = "Korean-History-Image-Collector/1.0 (personal research; low rate)"

CSV_FIELDS = [
    "순번",
    "이미지ID",
    "제목",
    "설명",
    "작성자",
    "시대",
    "유형",
    "분야",
    "이미지출처",
    "이용조건",
    "키워드",
    "관련콘텐츠",
    "목록분류",
    "목록요약",
    "썸네일URL",
    "원본이미지URL",
    "저장이미지파일",
    "상세요청URL",
]


def clean_text(value: str) -> str:
    value = html.unescape(value).replace("\xa0", " ").replace("\u3000", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def absolute_url(value: str) -> str:
    return urllib.parse.urljoin(BASE_URL, html.unescape(value))


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*]+', "_", value)
    return re.sub(r"\s+", " ", value).strip(" .")


class ListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []
        self.current: dict[str, str] | None = None
        self.link_depth = 0
        self.capture = ""
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        onclick = attr.get("onclick") or ""
        if tag == "a" and "fnViewDetail" in onclick:
            match = re.search(r"fnViewDetail\(\s*['\"]([^'\"]+)", onclick)
            if match:
                self.current = {
                    "id": match.group(1),
                    "title": "",
                    "category": "",
                    "summary": "",
                    "thumbnail": "",
                }
                self.link_depth = 1
                return

        if self.current is None:
            return
        if tag == "a":
            self.link_depth += 1
        classes = set((attr.get("class") or "").split())
        if tag == "img" and not self.current["thumbnail"]:
            self.current["thumbnail"] = absolute_url(attr.get("src") or "")
        if tag == "strong" and "sbj" in classes:
            self._start_capture("title")
        elif tag == "span" and "metaNew" in classes:
            self._start_capture("category")
        elif tag == "div" and "dsc" in classes:
            self._start_capture("summary")

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        if self.capture and tag in {"strong", "span", "div"}:
            self.current[self.capture] = clean_text("".join(self.parts))
            self.capture = ""
            self.parts = []
        if tag == "a":
            self.link_depth -= 1
            if self.link_depth == 0:
                self.items.append(self.current)
                self.current = None

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.parts.append(data)

    def _start_capture(self, name: str) -> None:
        self.capture = name
        self.parts = []


class DetailParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[str] = []
        self.title_parts: list[str] = []
        self.description_parts: list[str] = []
        self.writer_parts: list[str] = []
        self.metadata: dict[str, str] = {}
        self.related: list[str] = []
        self.title_depth = 0
        self.title_text_depth = 0
        self.text_depth = 0
        self.writer_depth = 0
        self.cell_depth = 0
        self.cell_label = ""
        self.cell_label_parts: list[str] = []
        self.cell_value_parts: list[str] = []
        self.label_depth = 0
        self.value_depth = 0
        self.current_href = ""
        self.current_link_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = set((attr.get("class") or "").split())

        if tag == "img":
            src = attr.get("src") or ""
            if "/data/img/ki/" in src and ".thumbnail." not in src:
                url = absolute_url(src)
                if url not in self.images:
                    self.images.append(url)

        if tag == "div" and "title" in classes:
            self.title_depth = 1
        elif self.title_depth and tag == "div":
            self.title_depth += 1
        if self.title_depth and tag == "strong":
            self.title_text_depth = 1

        if tag == "div" and "txt" in classes and not self.cell_depth:
            self.text_depth = 1
        elif self.text_depth and tag == "div":
            self.text_depth += 1

        if tag == "em" and attr.get("id") == "writer":
            self.writer_depth = 1

        if tag == "div" and "cell" in classes and not self.cell_depth:
            self.cell_depth = 1
            self.cell_label = ""
            self.cell_label_parts = []
            self.cell_value_parts = []
            return
        if self.cell_depth:
            if tag == "div":
                self.cell_depth += 1
            if tag == "em" and not self.cell_label:
                self.label_depth = 1
            elif tag == "div" and "tx" in classes:
                self.value_depth = 1
            elif self.value_depth and tag in {"br", "p", "li"}:
                self.cell_value_parts.append("\n")
            if self.value_depth and tag == "a":
                self.current_href = attr.get("href") or ""
                self.current_link_parts = []

    def handle_endtag(self, tag: str) -> None:
        if self.writer_depth and tag == "em":
            self.writer_depth = 0

        if self.label_depth and tag == "em":
            self.cell_label = clean_text("".join(self.cell_label_parts))
            self.label_depth = 0

        if self.current_href and tag == "a":
            link_text = clean_text("".join(self.current_link_parts))
            if self.cell_label == "관련 콘텐츠" and link_text:
                href = self.current_href
                if not href.lower().startswith("javascript:"):
                    href = absolute_url(href)
                self.related.append(f"{link_text} | {href}")
            self.current_href = ""
            self.current_link_parts = []

        if self.cell_depth and tag == "div":
            if self.value_depth:
                self.value_depth -= 1
            self.cell_depth -= 1
            if self.cell_depth == 0:
                label = self.cell_label or clean_text("".join(self.cell_label_parts))
                value = clean_text("".join(self.cell_value_parts))
                if label:
                    self.metadata[label] = value
                self.label_depth = 0
                self.value_depth = 0

        if self.text_depth and tag == "div":
            self.text_depth -= 1
        if self.title_depth and tag == "div":
            self.title_depth -= 1
        if self.title_text_depth and tag == "strong":
            self.title_text_depth = 0

    def handle_data(self, data: str) -> None:
        if self.writer_depth:
            self.writer_parts.append(data)
            return
        if self.label_depth:
            self.cell_label_parts.append(data)
            return
        if self.value_depth:
            self.cell_value_parts.append(data)
            if self.current_href:
                self.current_link_parts.append(data)
            return
        if self.title_text_depth:
            self.title_parts.append(data)
        elif self.text_depth:
            self.description_parts.append(data)

    def result(self) -> dict[str, object]:
        writer = clean_text("".join(self.writer_parts)).strip("[] ")
        return {
            "title": clean_text("".join(self.title_parts)),
            "description": clean_text("".join(self.description_parts)),
            "writer": writer,
            "images": self.images,
            "metadata": self.metadata,
            "related": "\n".join(dict.fromkeys(self.related)),
        }


class GentleClient:
    def __init__(self, delay: float, jitter: float, timeout: int, retries: int) -> None:
        self.delay = delay
        self.jitter = jitter
        self.timeout = timeout
        self.retries = retries
        self.last_request_at = 0.0
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor()
        )

    def _wait(self) -> None:
        wait = self.delay + random.uniform(0, self.jitter)
        elapsed = time.monotonic() - self.last_request_at
        if elapsed < wait:
            time.sleep(wait - elapsed)

    def request(
        self,
        url: str,
        data: bytes | None = None,
        accept: str = "text/html,application/xhtml+xml,*/*;q=0.8",
    ) -> bytes:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._wait()
            headers = {
                "User-Agent": USER_AGENT,
                "Accept": accept,
                "Accept-Language": "ko-KR,ko;q=0.9",
                "Referer": MAIN_URL,
            }
            if data is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            if "/front/ki/" in url:
                headers["X-Requested-With"] = "XMLHttpRequest"
            request = urllib.request.Request(url, data=data, headers=headers)
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    self.last_request_at = time.monotonic()
                    return response.read()
            except urllib.error.HTTPError as error:
                self.last_request_at = time.monotonic()
                last_error = error
                if error.code not in {429, 500, 502, 503, 504}:
                    break
                retry_after = error.headers.get("Retry-After", "")
                wait = float(retry_after) if retry_after.isdigit() else 15 * (2**attempt)
                print(f"서버 응답 {error.code}: {wait:.0f}초 후 재시도합니다.")
                time.sleep(wait)
            except (urllib.error.URLError, TimeoutError) as error:
                self.last_request_at = time.monotonic()
                last_error = error
                if attempt < self.retries:
                    wait = 10 * (2**attempt)
                    print(f"연결 오류: {wait:.0f}초 후 재시도합니다.")
                    time.sleep(wait)
        raise RuntimeError(f"요청 실패: {url}: {last_error}")

    def get_html(self, url: str) -> str:
        return self.request(url).decode("utf-8", errors="replace")


def parse_items(source: str) -> list[dict[str, str]]:
    parser = ListParser()
    parser.feed(source)
    unique: dict[str, dict[str, str]] = {}
    for item in parser.items:
        unique[item["id"]] = item
    return list(unique.values())


def find_total(source: str) -> int:
    patterns = [
        r"총\s*<[^>]+>\s*([\d,]+)\s*</[^>]+>\s*건",
        r"총\s*<[^>]+>\s*([\d,]+)\s*건",
        r"총\s*([\d,]+)\s*건",
        r"totalCount[^\d]{0,20}([\d,]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, source, flags=re.I)
        if match:
            return int(match.group(1).replace(",", ""))
    return 0


def read_completed(path: Path) -> tuple[set[str], int]:
    if not path.exists():
        return set(), 0
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    completed = {row["이미지ID"] for row in rows if row.get("이미지ID")}
    return completed, len(rows)


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


def image_path(output: Path, image_id: str, image_url: str) -> Path:
    suffix = Path(urllib.parse.urlparse(image_url).path).suffix or ".jpg"
    group = image_id.rsplit("_", 1)[0]
    return output / "images" / safe_filename(group) / f"{safe_filename(image_id)}{suffix}"


def download_image(
    client: GentleClient, output: Path, image_id: str, image_url: str
) -> str:
    path = image_path(output, image_id, image_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        data = client.request(image_url, accept="image/avif,image/webp,image/*,*/*;q=0.8")
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.write_bytes(data)
        temporary.replace(path)
    return str(path)


def list_page(client: GentleClient, page: int, first_html: str) -> list[dict[str, str]]:
    if page == 1:
        return parse_items(first_html)
    body = urllib.parse.urlencode(
        {"pageIndex": page, "pageUnit": 20, "blockSize": 10}
    ).encode("utf-8")
    return parse_items(client.request(LIST_URL, data=body).decode("utf-8", errors="replace"))


def detail_page(client: GentleClient, image_id: str) -> dict[str, object]:
    query = urllib.parse.urlencode({"levelId": image_id, "whereStr": ""})
    parser = DetailParser()
    parser.feed(client.get_html(f"{DETAIL_URL}?{query}"))
    return parser.result()


def metadata_value(metadata: dict[str, str], name: str) -> str:
    if name in metadata:
        value = metadata[name]
        if name == "키워드":
            return re.sub(r"\s*,\s*", ", ", value).replace("\n", " ").strip()
        return value
    compact = name.replace(" ", "")
    for key, value in metadata.items():
        if key.replace(" ", "") == compact:
            if name == "키워드":
                return re.sub(r"\s*,\s*", ", ", value).replace("\n", " ").strip()
            return value
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="우리역사넷 한국사 이미지 자료 전체를 CSV와 이미지 파일로 저장합니다."
    )
    parser.add_argument("--output", type=Path, default=RAW_DATA_DIR / "한국사 이미지 자료")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--end-page", type=int, default=0, help="0이면 마지막 페이지까지")
    parser.add_argument("--limit", type=int, default=0, help="시험용 신규 저장 건수")
    parser.add_argument("--delay", type=float, default=5.0, help="요청 사이 최소 대기 시간")
    parser.add_argument("--jitter", type=float, default=2.0, help="추가 무작위 대기 시간")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="CSV 정보만 저장하고 실제 이미지 파일은 받지 않습니다.",
    )
    args = parser.parse_args()

    if args.delay < 2:
        parser.error("서버 보호를 위해 --delay는 2초 이상이어야 합니다.")
    if args.start_page < 1 or args.end_page < 0 or args.limit < 0 or args.jitter < 0:
        parser.error("페이지와 제한 값은 올바른 양수여야 합니다.")

    client = GentleClient(args.delay, args.jitter, args.timeout, args.retries)
    print("목록과 세션 정보를 확인합니다...")
    first_html = client.get_html(MAIN_URL)
    total = find_total(first_html)
    first_items = parse_items(first_html)
    if not first_items:
        raise SystemExit("첫 목록에서 이미지 항목을 찾지 못했습니다. 사이트 구조를 확인해 주십시오.")
    if not total:
        total = 2265
        print("전체 건수를 읽지 못해 현재 확인된 2,265건을 사용합니다.")

    total_pages = (total + 19) // 20
    end_page = args.end_page or total_pages
    if end_page > total_pages or args.start_page > end_page:
        parser.error(f"페이지 범위는 1~{total_pages}여야 합니다.")

    csv_path = args.output / "한국사_이미지_자료.csv"
    completed, existing_count = read_completed(csv_path)
    saved = 0
    failed = 0
    order = existing_count
    print(
        f"전체 {total:,}건, {total_pages}페이지 / 기존 {existing_count:,}건 / "
        f"수집 범위 {args.start_page}~{end_page}페이지"
    )

    for page in range(args.start_page, end_page + 1):
        items = first_items if page == 1 else list_page(client, page, first_html)
        if not items:
            print(f"[{page}/{total_pages}] 목록이 비어 있어 중단합니다.")
            break
        print(f"\n[{page}/{total_pages}] {len(items)}건 확인")

        for item in items:
            image_id = item["id"]
            if image_id in completed:
                continue
            if args.limit and saved >= args.limit:
                print(f"\n시험 제한 {args.limit:,}건에 도달했습니다.")
                print(f"완료: 신규 {saved:,}건, 실패 {failed:,}건 -> {csv_path}")
                return
            try:
                detail = detail_page(client, image_id)
                metadata = dict(detail["metadata"])
                images = list(detail["images"])
                image_url = images[0] if images else ""
                saved_file = ""
                if image_url and not args.metadata_only:
                    saved_file = download_image(client, args.output, image_id, image_url)

                order += 1
                append_csv(
                    csv_path,
                    {
                        "순번": str(order),
                        "이미지ID": image_id,
                        "제목": str(detail["title"] or item["title"]),
                        "설명": str(detail["description"] or item["summary"]),
                        "작성자": str(detail["writer"]),
                        "시대": metadata_value(metadata, "시대"),
                        "유형": metadata_value(metadata, "유형"),
                        "분야": metadata_value(metadata, "분야"),
                        "이미지출처": metadata_value(metadata, "이미지출처"),
                        "이용조건": metadata_value(metadata, "이용조건"),
                        "키워드": metadata_value(metadata, "키워드"),
                        "관련콘텐츠": str(detail["related"] or metadata_value(metadata, "관련 콘텐츠")),
                        "목록분류": item["category"],
                        "목록요약": item["summary"],
                        "썸네일URL": item["thumbnail"],
                        "원본이미지URL": image_url,
                        "저장이미지파일": saved_file,
                        "상세요청URL": f"{DETAIL_URL}?{urllib.parse.urlencode({'levelId': image_id, 'whereStr': ''})}",
                    },
                )
                completed.add(image_id)
                saved += 1
                print(f"  저장 {order:,}: {detail['title'] or item['title'] or image_id}")
            except (RuntimeError, OSError) as error:
                failed += 1
                print(f"  실패 {image_id}: {error}")

    print(f"\n완료: 신규 {saved:,}건, 실패 {failed:,}건 -> {csv_path}")


if __name__ == "__main__":
    main()
