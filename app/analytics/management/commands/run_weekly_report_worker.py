"""주간 리포트 생성 워커.

pending 리포트를 폴링해 멀티에이전트 그래프로 문구를 만든다.
실행: python manage.py run_weekly_report_worker --interval 10
"""

from __future__ import annotations

import logging
import signal
import time
from datetime import datetime
from typing import Callable, Mapping

from django.core.management.base import BaseCommand, CommandParser
from django.db import close_old_connections
from django.utils import timezone

from analytics.service.weekly_report import repository
from analytics.service.weekly_report.collector import (
    enqueue_weekly_report,
    find_recoverable_sessions,
)
from analytics.service.weekly_report.config import (
    WeeklyReportConfig,
    get_weekly_report_config,
)
from analytics.service.weekly_report.llm import generate_default_report_content


logger = logging.getLogger(__name__)

ContentGenerator = Callable[
    [Mapping[str, object], WeeklyReportConfig, "str | None"],
    Mapping[str, object],
]

IDLE = "idle"
READY = "ready"
RETRIED = "retried"


def process_one_report(
    config: WeeklyReportConfig,
    generate_content: ContentGenerator,
    clock: Callable[[], datetime] = timezone.now,
) -> str:
    """리포트 1건을 처리하고 결과 코드를 돌려준다.

    clock 을 두 번 부른다. claim 시각을 finish/retry 에 재사용하면
    availableAt 이 쓰는 순간 이미 과거가 되어 백오프가 무효화된다.
    LLM 왕복이 첫 재시도 지연(30초)보다 길기 때문이다.
    """
    claimed = repository.claim_next_report(clock(), config)
    if claimed is None:
        return IDLE

    study_plan_id = int(claimed["studyPlanId"])
    report = claimed["report"]
    attempt_count = int(report["worker"]["attemptCount"])
    logger.info("주간 리포트 획득 plan=%s attempt=%s", study_plan_id, attempt_count)

    try:
        content = generate_content(report["result"], config, report.get("reportType"))
        if not isinstance(content, Mapping):
            raise TypeError("생성기가 content 를 돌려주지 않았습니다.")
    except Exception:
        logger.exception("주간 리포트 생성 실패 plan=%s", study_plan_id)
        return _record_failure(study_plan_id, attempt_count, "GENERATOR_ERROR", config, clock)

    if not content.get("fallbackUsed"):
        if not repository.finish_report(study_plan_id, attempt_count, content, clock()):
            logger.info("다른 워커가 먼저 처리했습니다. plan=%s", study_plan_id)
        return READY
    return _record_failure(study_plan_id, attempt_count, "AI_FALLBACK", config, clock, content)


def scan_missing_reports() -> int:
    """트리거가 실패해 리포트가 없는 계획을 주워 담는다. 만든 건수를 돌려준다."""
    created_count = 0
    for candidate in find_recoverable_sessions():
        created = enqueue_weekly_report(
            int(candidate["userId"]),
            int(candidate["sourceSessionId"]),
            int(candidate["studyPlanId"]),
        )
        if created:
            created_count += 1
    return created_count


def _record_failure(
    study_plan_id: int,
    attempt_count: int,
    error_code: str,
    config: WeeklyReportConfig,
    clock: Callable[[], datetime],
    content: Mapping[str, object] | None = None,
) -> str:
    """재시도를 예약하거나, 마지막 시도면 기본 문구로 확정한다.

    마지막 시도에서 retry_report 를 부르면 status 가 failed 가 되어
    사용자에게 내용이 빈 리포트가 나간다. 조건을 명시적으로 적어 둔다.
    """
    if attempt_count < config.maximum_attempt_count:
        repository.retry_report(study_plan_id, attempt_count, error_code, clock(), config)
        return RETRIED
    elif attempt_count >= config.maximum_attempt_count:
        fallback_content = content or _build_empty_fallback_content(error_code)
        repository.finish_report(study_plan_id, attempt_count, fallback_content, clock())
        logger.warning("주간 리포트를 기본 문구로 확정 plan=%s", study_plan_id)
        return READY
    return RETRIED


def _build_empty_fallback_content(error_code: str) -> dict[str, object]:
    """생성기가 예외로 죽어 content 조차 없을 때 쓸 최소 문구."""
    from analytics.service.weekly_report.service import build_fallback_content

    content = build_fallback_content({})
    content["validation"] = {"guard": "fallback", "validator": error_code}
    return content


class Command(BaseCommand):
    help = "주간 리포트 pending 건을 폴링해 멀티에이전트 그래프로 생성한다."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--interval", type=int, default=10, help="폴링 간격(초)")
        parser.add_argument("--batch-size", type=int, default=1, help="한 번에 처리할 건수")
        parser.add_argument(
            "--recovery-every",
            type=int,
            default=60,
            help="몇 번 폴링마다 복구 스캔을 돌릴지. 0 이면 하지 않는다",
        )
        parser.add_argument("--once", action="store_true", help="1회만 돌고 종료")

    def handle(self, *args: object, **options: object) -> None:
        config = get_weekly_report_config()
        interval = int(options["interval"])
        batch_size = int(options["batch_size"])
        recovery_every = int(options["recovery_every"])
        run_once = bool(options["once"])

        self._stop_requested = False
        signal.signal(signal.SIGINT, self._request_stop)
        signal.signal(signal.SIGTERM, self._request_stop)
        self._warn_if_model_is_unset(config)

        loop_count = 0
        while not self._stop_requested:
            # 장시간 떠 있는 프로세스라 만료된 DB 커넥션을 직접 정리해야 한다.
            close_old_connections()
            loop_count += 1
            # 장시간 도는 프로세스라 일시적인 DB 오류로 죽으면 안 된다.
            try:
                self._process_batch(config, batch_size)
                if recovery_every > 0 and loop_count % recovery_every == 0:
                    created_count = scan_missing_reports()
                    if created_count:
                        self.stdout.write(f"[weekly-report] 복구 {created_count}건")
            except Exception:
                logger.exception("워커 루프에서 예외가 발생했습니다. 다음 주기에 계속합니다.")
            if run_once:
                break
            time.sleep(interval)

    def _process_batch(self, config: WeeklyReportConfig, batch_size: int) -> None:
        for _ in range(batch_size):
            code = process_one_report(config, generate_default_report_content)
            if code == IDLE:
                break
            self.stdout.write(f"[weekly-report] {code}")

    def _request_stop(self, signal_number: int, frame: object) -> None:
        self._stop_requested = True
        self.stdout.write("[weekly-report] 종료 요청을 받았습니다. 현재 건을 마칩니다.")

    def _warn_if_model_is_unset(self, config: WeeklyReportConfig) -> None:
        """모델 이름이 비어 있으면 매번 fallback 으로 흘러 조용히 시간만 쓴다."""
        if config.model == "configured-model":
            self.stderr.write(
                "[weekly-report] WEEKLY_REPORT_LLM_MODEL / OPENAI_CHAT_MODEL 이 없습니다. "
                "LLM 호출이 실패해 기본 문구만 생성됩니다.",
            )
