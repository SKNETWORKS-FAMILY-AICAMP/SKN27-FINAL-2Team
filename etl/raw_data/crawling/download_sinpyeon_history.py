from __future__ import annotations

import argparse
import csv
import hashlib
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
START_URL = f"{BASE_URL}/front/nh/view.do"
USER_AGENT = "Sinpyeon-Korean-History-CSV-Collector/1.0 (personal research; low rate)"

CSV_FIELDS = [
    "권번호",
    "권명",
    "페이지순서",
    "제목",
    "본문",
    "각주",
    "이미지설명",
    "이미지URL",
    "이미지파일",
    "페이지ID",
    "원본URL",
]


def clean_text(value: str) -> str:
    value = html.unescape(value).replace("\xa0", " ").replace("\u3000", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*]+', "_", value)
    return re.sub(r"\s+", " ", value).strip(" .")


def absolute_url(value: str) -> str:
    return urllib.parse.urljoin(BASE_URL, html.unescape(value))


class MainPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_link = False
        self.href = ""
        self.text: list[str] = []
        self.volumes: list[tuple[int, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "a" and "levelId=nh_" in (attr.get("href") or ""):
            self.in_link = True
            self.href = attr.get("href") or ""
            self.text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.in_link:
            match = re.search(r"levelId=nh_(\d{3})_0010(?:&|$)", self.href)
            if match:
                number = int(match.group(1))
                label = clean_text("".join(self.text))
                label = re.sub(r"^\d{2}\s*", "", label)
                self.volumes.append((number, label, absolute_url(self.href)))
            self.in_link = False

    def handle_data(self, data: str) -> None:
        if self.in_link:
            self.text.append(data)


class ContentPageParser(HTMLParser):
    BLOCK_TAGS = {
        "address",
        "article",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "p",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.article_depth = 0
        self.capture_article = False
        self.skip_depth = 0
        self.footnote_depth = 0
        self.heading_depth = 0
        self.caption_depth = 0
        self.explanation_depth = 0
        self.body_parts: list[str] = []
        self.footnote_parts: list[str] = []
        self.heading_parts: list[str] = []
        self.current_heading: list[str] = []
        self.current_caption: list[str] = []
        self.current_explanation: list[str] = []
        self.images: list[dict[str, str]] = []
        self.next_url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = set((attr.get("class") or "").split())

        if tag == "a" and attr.get("title") == "다음":
            href = attr.get("href") or ""
            if href and not href.lower().startswith("javascript:"):
                self.next_url = absolute_url(href)

        if tag == "div" and "article" in classes:
            self.capture_article = True
            self.article_depth = 1
            return
        if not self.capture_article:
            return

        if tag == "div":
            self.article_depth += 1

        if self.skip_depth:
            self.skip_depth += 1
            return

        if tag in {"script", "style", "noscript"}:
            self.skip_depth = 1
            return
        if tag == "a" and "footnote" in classes:
            self.skip_depth = 1
            return

        if tag == "div" and "footnote_box" in classes:
            self.footnote_depth = 1
            return
        if self.footnote_depth and tag == "div":
            self.footnote_depth += 1

        if tag in {"h2", "h3", "h4", "h5", "h6"} and not self.footnote_depth:
            self.heading_depth = 1
            self.current_heading = []

        if tag == "div" and "img_caption_m" in classes:
            self.caption_depth = 1
            self.current_caption = []
        elif self.caption_depth and tag == "div":
            self.caption_depth += 1

        if tag == "div" and "img_explanation_m" in classes:
            self.explanation_depth = 1
            self.current_explanation = []
        elif self.explanation_depth and tag == "div":
            self.explanation_depth += 1

        if tag == "img":
            src = attr.get("src") or ""
            if src and "/data/img/" in src:
                self.images.append(
                    {
                        "url": absolute_url(src),
                        "caption": "",
                        "explanation": "",
                    }
                )

        if tag in self.BLOCK_TAGS:
            self._append_break()

    def handle_endtag(self, tag: str) -> None:
        if not self.capture_article:
            return

        if self.skip_depth:
            self.skip_depth -= 1
            return

        if self.caption_depth and tag == "div":
            self.caption_depth -= 1
            if self.caption_depth == 0 and self.images:
                self.images[-1]["caption"] = clean_text("".join(self.current_caption))

        if self.explanation_depth and tag == "div":
            self.explanation_depth -= 1
            if self.explanation_depth == 0 and self.images:
                self.images[-1]["explanation"] = clean_text(
                    "".join(self.current_explanation)
                )

        if self.heading_depth and tag in {"h2", "h3", "h4", "h5", "h6"}:
            heading = clean_text("".join(self.current_heading))
            if heading:
                self.heading_parts.append(heading)
            self.heading_depth = 0

        if tag in self.BLOCK_TAGS:
            self._append_break()

        if self.footnote_depth and tag == "div":
            self.footnote_depth -= 1

        if tag == "div":
            self.article_depth -= 1
            if self.article_depth == 0:
                self.capture_article = False

    def handle_data(self, data: str) -> None:
        if not self.capture_article or self.skip_depth:
            return
        if self.heading_depth:
            self.current_heading.append(data)
        if self.caption_depth:
            self.current_caption.append(data)
        if self.explanation_depth:
            self.current_explanation.append(data)
        if self.footnote_depth:
            self.footnote_parts.append(data)
        elif not self.caption_depth and not self.explanation_depth:
            self.body_parts.append(data)

    def _append_break(self) -> None:
        target = self.footnote_parts if self.footnote_depth else self.body_parts
        target.append("\n")

    def result(self) -> dict[str, object]:
        title = " > ".join(dict.fromkeys(self.heading_parts))
        descriptions = []
        urls = []
        for image in self.images:
            description = image["caption"]
            if image["explanation"]:
                description = f"{description} | {image['explanation']}".strip(" |")
            descriptions.append(description)
            urls.append(image["url"])
        return {
            "title": title,
            "body": clean_text("".join(self.body_parts)),
            "footnotes": clean_text("".join(self.footnote_parts)),
            "image_descriptions": "\n".join(descriptions),
            "image_urls": "\n".join(urls),
            "images": self.images,
            "next_url": self.next_url,
        }


class GentleClient:
    def __init__(self, delay: float, jitter: float, timeout: int, retries: int) -> None:
        self.delay = delay
        self.jitter = jitter
        self.timeout = timeout
        self.retries = retries
        self.last_request_at = 0.0

    def _wait(self) -> None:
        wait = self.delay + random.uniform(0, self.jitter)
        elapsed = time.monotonic() - self.last_request_at
        if elapsed < wait:
            time.sleep(wait - elapsed)

    def get(self, url: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._wait()
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,image/*;q=0.8",
                    "Accept-Language": "ko-KR,ko;q=0.9",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    self.last_request_at = time.monotonic()
                    return response.read()
            except urllib.error.HTTPError as error:
                self.last_request_at = time.monotonic()
                last_error = error
                if error.code not in {429, 500, 502, 503, 504}:
                    break
                retry_after = error.headers.get("Retry-After")
                wait = (
                    float(retry_after)
                    if retry_after and retry_after.isdigit()
                    else 15 * (2**attempt)
                )
                print(f"서버 대기 응답 {error.code}: {wait:.0f}초 후 재시도")
                time.sleep(wait)
            except (urllib.error.URLError, TimeoutError) as error:
                self.last_request_at = time.monotonic()
                last_error = error
                if attempt < self.retries:
                    wait = 10 * (2**attempt)
                    print(f"연결 오류: {wait:.0f}초 후 재시도")
                    time.sleep(wait)
        raise RuntimeError(f"요청 실패: {url}: {last_error}")


def parse_level_id(url: str) -> str:
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return query.get("levelId", [""])[0]


def read_completed(path: Path) -> tuple[set[str], int]:
    if not path.exists():
        return set(), 0
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    return {row["페이지ID"] for row in rows if row.get("페이지ID")}, len(rows)


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


def image_filename(url: str, index: int) -> str:
    parsed = urllib.parse.urlparse(url)
    name = Path(parsed.path).name
    if not name:
        name = f"image_{index:03d}_{hashlib.sha1(url.encode()).hexdigest()[:10]}.jpg"
    return safe_filename(name)


def download_images(
    client: GentleClient,
    images: list[dict[str, str]],
    output_dir: Path,
) -> list[str]:
    saved: list[str] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, image in enumerate(images, start=1):
        url = image["url"]
        path = output_dir / image_filename(url, index)
        if not path.exists() or path.stat().st_size == 0:
            path.write_bytes(client.get(url))
        saved.append(str(path))
    return saved


def discover_volumes(client: GentleClient) -> list[tuple[int, str, str]]:
    data = client.get(f"{BASE_URL}/front/nh/main.do")
    parser = MainPageParser()
    parser.feed(data.decode("utf-8", errors="replace"))
    unique = {number: (number, title, url) for number, title, url in parser.volumes}
    return [unique[number] for number in sorted(unique)]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="우리역사넷 신편 한국사 전52권의 본문을 권별 CSV로 저장합니다."
    )
    parser.add_argument("--output", type=Path, default=RAW_DATA_DIR / "신편 한국사 csv")
    parser.add_argument("--volume", type=int, default=0, help="특정 권만 수집(1~52)")
    parser.add_argument("--start-volume", type=int, default=1)
    parser.add_argument("--end-volume", type=int, default=52)
    parser.add_argument("--limit", type=int, default=0, help="시험용 최대 페이지 수")
    parser.add_argument("--delay", type=float, default=5.0, help="요청 사이 최소 대기 시간")
    parser.add_argument("--jitter", type=float, default=2.0, help="추가 무작위 대기 시간")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument(
        "--download-images",
        action="store_true",
        help="본문 이미지 파일도 내려받기(요청량과 시간이 크게 증가함)",
    )
    args = parser.parse_args()

    if args.volume:
        args.start_volume = args.end_volume = args.volume
    if not 1 <= args.start_volume <= args.end_volume <= 52:
        parser.error("권 범위는 1~52여야 합니다.")
    if args.delay < 2:
        parser.error("서버 보호를 위해 --delay는 2초 이상이어야 합니다.")
    if args.jitter < 0 or args.limit < 0:
        parser.error("--jitter와 --limit는 0 이상이어야 합니다.")

    client = GentleClient(args.delay, args.jitter, args.timeout, args.retries)
    volumes = discover_volumes(client)
    if len(volumes) != 52:
        raise SystemExit(f"52권 목차를 찾지 못했습니다: 현재 {len(volumes)}권")

    remaining = args.limit
    total_saved = 0
    total_failed = 0

    for number, volume_title, start_url in volumes:
        if not args.start_volume <= number <= args.end_volume:
            continue

        csv_path = args.output / safe_filename(f"{number:02d}권_{volume_title}.csv")
        completed, existing_count = read_completed(csv_path)
        page_order = existing_count
        current_url = start_url
        visited: set[str] = set()
        prefix = f"nh_{number:03d}"
        print(f"\n[{number}/52] {volume_title} (기존 {existing_count:,}페이지)")

        while current_url:
            page_id = parse_level_id(current_url)
            if not page_id.startswith(prefix) or page_id in visited:
                break
            visited.add(page_id)

            try:
                raw = client.get(current_url)
                content = ContentPageParser()
                content.feed(raw.decode("utf-8", errors="replace"))
                result = content.result()
                next_url = str(result["next_url"])

                if page_id not in completed:
                    if args.limit and remaining <= 0:
                        print(f"시험 제한 {args.limit:,}페이지에 도달했습니다.")
                        print(f"완료: 신규 {total_saved:,}, 실패 {total_failed:,}")
                        return

                    page_order += 1
                    image_files: list[str] = []
                    if args.download_images and result["images"]:
                        image_files = download_images(
                            client,
                            list(result["images"]),
                            args.output / "images" / f"{number:02d}권" / page_id,
                        )

                    append_csv(
                        csv_path,
                        {
                            "권번호": str(number),
                            "권명": volume_title,
                            "페이지순서": str(page_order),
                            "제목": str(result["title"]),
                            "본문": str(result["body"]),
                            "각주": str(result["footnotes"]),
                            "이미지설명": str(result["image_descriptions"]),
                            "이미지URL": str(result["image_urls"]),
                            "이미지파일": "\n".join(image_files),
                            "페이지ID": page_id,
                            "원본URL": current_url,
                        },
                    )
                    completed.add(page_id)
                    total_saved += 1
                    if args.limit:
                        remaining -= 1
                    print(f"  {page_order} 저장: {result['title'] or page_id}")

                current_url = next_url
            except RuntimeError as error:
                total_failed += 1
                print(f"  실패 {page_id}: {error}")
                break

    print(f"\n완료: 신규 {total_saved:,}페이지, 실패 {total_failed:,}건 -> {args.output}")


if __name__ == "__main__":
    main()
