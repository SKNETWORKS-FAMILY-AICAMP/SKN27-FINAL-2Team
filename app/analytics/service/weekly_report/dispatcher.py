"""주간복습 완료 직후 리포트를 예약하고 곧바로 생성한다.

제출 응답을 붙잡지 않으려고 생성은 별도 스레드에서 돌린다. 사용자가 결과
페이지를 보는 동안 끝나므로, 마이페이지에 도착하면 리포트가 이미 있다.

스레드가 죽거나 프로세스가 재시작돼도 리포트는 pending 으로 남는다.
run_weekly_report_worker 의 복구 스캔이 그것을 다시 집는다. 즉 이 파일은
빠른 경로이고, 워커가 안전망이다.

운영에서 웹 프로세스가 LLM 호출까지 떠안는 것이 부담이면
WEEKLY_REPORT_INLINE_GENERATION=0 으로 끄고 워커만 쓴다.
"""

from __future__ import annotations

import logging
import threading
import time

from analytics.service.weekly_report.config import (
    WeeklyReportConfig,
    get_weekly_report_config,
)


logger = logging.getLogger(__name__)


def dispatch_weekly_report(
    user_id: int,
    source_session_id: int,
    study_plan_id: int,
    config: WeeklyReportConfig | None = None,
) -> bool:
    """리포트를 예약하고 생성 스레드를 띄운다. 새로 예약했으면 True.

    반드시 주간복습 블록 완료가 커밋된 뒤에 불러야 한다. 커밋 전에 부르면
    근거 수집이 아직 완료되지 않은 계획 상태를 읽는다.
    """
    from analytics.service.weekly_report.collector import enqueue_weekly_report

    resolved_config = config or get_weekly_report_config()
    created = enqueue_weekly_report(
        user_id,
        source_session_id,
        study_plan_id,
        config=resolved_config,
    )
    if not created:
        return False
    if not resolved_config.inline_generation_enabled:
        return True

    thread = threading.Thread(
        target=_generate_in_background,
        args=(resolved_config, study_plan_id),
        name=f"weekly-report-{study_plan_id}",
        daemon=True,
    )
    thread.start()
    return True


def _generate_in_background(config: WeeklyReportConfig, study_plan_id: int) -> None:
    """리포트가 확정될 때까지 워커와 같은 처리 단계를 반복한다.

    한 번만 돌리면 안 된다. 생성이 실패하면 상태가 pending 으로 되돌아가고
    재시도는 다음 폴링에서 일어나는데, 워커를 띄우지 않았다면 그 폴링이
    영원히 오지 않아 화면이 "작성 중" 에 갇힌다. 그래서 설정된 재시도 간격만큼
    기다렸다가 여기서 직접 다시 시도한다. 마지막 시도에서는 코드가 만든
    대체 문구로 확정되므로, 어떤 경우에도 pending 으로 남지 않는다.

    잡는 대상이 방금 예약한 건이 아닐 수도 있다. 행 잠금이 중복 처리를 막고,
    남은 건은 다음 호출이나 워커가 가져가므로 문제가 되지 않는다.

    스레드는 요청과 별개의 DB 커넥션을 쓴다. 끝나면 직접 닫아야 커넥션이 샌다.
    """
    from django.db import connections

    from analytics.management.commands.run_weekly_report_worker import (
        IDLE,
        READY,
        process_one_report,
    )
    from analytics.service.weekly_report.llm import generate_default_report_content

    try:
        for attempt_index in range(config.maximum_attempt_count):
            code = process_one_report(config, generate_default_report_content)
            logger.info(
                "주간 리포트 즉시 생성 plan=%s 시도=%s 결과=%s",
                study_plan_id,
                attempt_index + 1,
                code,
            )
            if code in (READY, IDLE):
                break
            delay_index = min(attempt_index, len(config.retry_delays_seconds) - 1)
            time.sleep(config.retry_delays_seconds[delay_index])
    except Exception:
        logger.exception("주간 리포트 즉시 생성 실패 plan=%s", study_plan_id)
    finally:
        connections.close_all()
