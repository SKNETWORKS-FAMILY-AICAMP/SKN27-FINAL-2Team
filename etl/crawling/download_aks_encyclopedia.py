from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "etl" / "raw_data"
OUTPUT_DIR = RAW_DATA_DIR / "한국민족문화대백과사전"
BASE_URL = "https://devin.aks.ac.kr:8080/api"
USER_AGENT = "AKS-Encyclopedia-Collector/1.0 (research; low rate)"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def request_json(path: str, api_key: str, params: dict[str, Any] | None = None, timeout: int = 10) -> Any:
    query = urllib.parse.urlencode(params or {})
    url = f"{BASE_URL}{path}"
    if query:
        url = f"{url}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "X-API-Key": api_key,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (TimeoutError, urllib.error.URLError) as error:
            last_error = error
            print(f"retry {attempt}/3: {url} ({error})")
            time.sleep(attempt)
    raise last_error or RuntimeError(f"request failed: {url}")


def extract_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("items", "data", "list", "content", "results"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = extract_items(value)
            if nested:
                return nested
    return []


def append_jsonl(path: Path, rows: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def collect_list(kind: str, api_key: str, page_size: int, limit: int, sleep: float, timeout: int) -> list[str]:
    path = "/articles" if kind == "articles" else "/medias"
    id_key = "eid" if kind == "articles" else "mid"
    output_path = OUTPUT_DIR / f"{kind}_list.jsonl"
    seen_ids = list(dict.fromkeys(str(row.get(id_key)) for row in read_jsonl(output_path) if row.get(id_key)))
    if limit and len(seen_ids) >= limit:
        return seen_ids[:limit]
    page = (len(seen_ids) // page_size) + 1
    if seen_ids:
        print(f"{kind} list resume ids={len(seen_ids)} page={page}")

    while True:
        data = request_json(path, api_key, {"p": page, "ps": page_size}, timeout)
        items = extract_items(data)
        if not items:
            break

        append_jsonl(output_path, items)
        for item in items:
            item_id = item.get(id_key)
            if item_id:
                seen_ids.append(str(item_id))

        print(f"{kind} page={page} rows={len(items)} total={len(seen_ids)}")
        if limit and len(seen_ids) >= limit:
            return seen_ids[:limit]

        page += 1
        time.sleep(sleep)

    return seen_ids


def collect_details(kind: str, api_key: str, item_ids: list[str], sleep: float, timeout: int) -> None:
    path_prefix = "/articles" if kind == "articles" else "/medias"
    output_path = OUTPUT_DIR / f"{kind}_detail.jsonl"

    id_key = "eid" if kind == "articles" else "mid"
    done_ids = {str(row.get(id_key)) for row in read_jsonl(output_path) if row.get(id_key)}
    todo_ids = [item_id for item_id in item_ids if item_id not in done_ids]
    print(f"{kind} details skip={len(done_ids)} todo={len(todo_ids)}")

    for index, item_id in enumerate(todo_ids, start=1):
        try:
            data = request_json(f"{path_prefix}/{urllib.parse.quote(item_id)}", api_key, timeout=timeout)
            append_jsonl(output_path, [data])
            print(f"{kind} detail {index}/{len(todo_ids)} id={item_id}")
        except urllib.error.HTTPError as error:
            append_jsonl(OUTPUT_DIR / f"{kind}_errors.jsonl", [{"id": item_id, "error": str(error)}])
        time.sleep(sleep)


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description="한국민족문화대백과사전 OpenAPI 수집")
    parser.add_argument("--api-key", default=os.getenv("AKS_API_KEY"), help="OpenAPI key. 기본값: AKS_API_KEY")
    parser.add_argument("--kind", choices=("articles", "medias", "both"), default="articles")
    parser.add_argument("--details", action="store_true", help="목록 수집 후 상세 데이터도 수집")
    parser.add_argument("--skip-list", action="store_true", help="기존 목록 JSONL만 사용")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--limit", type=int, default=0, help="0이면 끝까지 수집")
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=10)
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("API key가 필요합니다. --api-key 또는 AKS_API_KEY를 사용하세요.")

    kinds = ("articles", "medias") if args.kind == "both" else (args.kind,)
    for kind in kinds:
        if args.skip_list:
            id_key = "eid" if kind == "articles" else "mid"
            ids = [str(row.get(id_key)) for row in read_jsonl(OUTPUT_DIR / f"{kind}_list.jsonl") if row.get(id_key)]
            print(f"{kind} list reuse={len(ids)}")
        else:
            ids = collect_list(kind, args.api_key, args.page_size, args.limit, args.sleep, args.timeout)
        if args.details:
            collect_details(kind, args.api_key, ids, args.sleep, args.timeout)


if __name__ == "__main__":
    main()
