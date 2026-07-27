#!/usr/bin/env python3
"""1단 스냅샷 품질 검증 리포트

수집된 전체 스냅샷을 훑어 스펙 §8의 품질 항목을 검사한다.
KRX에 요청하지 않고 저장된 parquet만 읽으며, 데이터를 수정하지 않는다.

전체를 메모리에 올리지 않고 일자별로 읽어 집계만 누적한다.

실행 방법은 docs/COMMANDS.md 참고.
"""

import sys
from datetime import datetime

import pandas as pd

from krx_sprint.collect.meta_store import load_holidays
from krx_sprint.collect.quality import QualityIssue, Severity, TickerTracker, check_coverage, check_daily_snapshot
from krx_sprint.collect.snapshot_store import list_collected_dates, load_snapshot
from krx_sprint.common_constants import (
    CACHE_DIR,
    COL_TICKER,
    COLLECTION_START_DATE,
    HOLIDAYS_JSON_PATH,
    KST,
)
from krx_sprint.utils.cli_helpers import cli_exception_handler
from krx_sprint.utils.formatting import Align, TableLogger
from krx_sprint.utils.logger import get_logger
from krx_sprint.utils.meta_manager import save_metadata

logger = get_logger()

# meta.json 최상위 키 (scripts/CLAUDE.md 메타데이터 지원 타입)
KEY_META_TYPE = "snapshot_quality"

# 진행 상황 로그 간격 (일자 수)
PROGRESS_INTERVAL = 200

# 요약 테이블 컬럼 정의
SUMMARY_TABLE_COLUMNS = [
    ("심각도", 10, Align.LEFT),
    ("검사 항목", 20, Align.LEFT),
    ("건수", 10, Align.RIGHT),
]

# 상세 CSV 컬럼
CSV_COLUMNS = ["severity", "category", "target", "detail"]


def _scan_snapshots() -> tuple[list[QualityIssue], int, int]:
    """저장된 스냅샷을 일자별로 훑어 이슈를 모은다.

    Returns:
        (이슈 목록, 검사한 일자 수, 누적 종목 행 수)
    """
    collected = sorted(list_collected_dates())
    if not collected:
        raise ValueError("수집된 스냅샷이 없습니다. 먼저 collect_snapshots.py를 실행하십시오")

    holidays = load_holidays(HOLIDAYS_JSON_PATH)
    issues: list[QualityIssue] = list(check_coverage(COLLECTION_START_DATE, collected[-1], set(collected), holidays))

    tracker = TickerTracker()
    row_count = 0

    for index, target in enumerate(collected, start=1):
        snapshot = load_snapshot(target)
        row_count += len(snapshot)

        issues.extend(check_daily_snapshot(snapshot, target))
        issues.extend(tracker.observe(target, snapshot[COL_TICKER]))

        if index % PROGRESS_INTERVAL == 0:
            logger.debug("검사 진행: %d/%d일 (%s)", index, len(collected), target)

    issues.extend(tracker.finalize(collected[-1]))

    return issues, len(collected), row_count


def _save_detail_csv(issues: list[QualityIssue]) -> "None | str":
    """이슈 상세를 CSV로 저장한다.

    Args:
        issues: 저장할 이슈 목록

    Returns:
        저장 경로 문자열 (이슈가 없으면 None)
    """
    if not issues:
        return None

    frame = pd.DataFrame(
        [[issue.severity.value, issue.category, issue.target, issue.detail] for issue in issues],
        columns=CSV_COLUMNS,
    )

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"quality_report_{datetime.now(KST).strftime('%Y%m%d_%H%M')}.csv"
    frame.to_csv(path, index=False, encoding="utf-8")

    return str(path)


def _print_summary(issues: list[QualityIssue], date_count: int, row_count: int) -> None:
    """검사 결과를 표로 출력한다."""
    counts: dict[tuple[str, str], int] = {}
    for issue in issues:
        key = (issue.severity.value, issue.category)
        counts[key] = counts.get(key, 0) + 1

    rows = [[severity, category, count] for (severity, category), count in sorted(counts.items())]
    if not rows:
        rows = [["-", "이슈 없음", 0]]

    table = TableLogger(SUMMARY_TABLE_COLUMNS, logger)
    table.print_table(rows, title=f"스냅샷 품질 검사 결과 ({date_count}일 / {row_count:,}행)")

    for issue in issues:
        if issue.severity is Severity.ERROR:
            logger.warning("[%s] %s: %s", issue.category, issue.target, issue.detail)


@cli_exception_handler
def main() -> int:
    """전체 스냅샷을 검사하고 결과를 요약한다.

    Returns:
        종료 코드 (0=오류 없음, 1=오류 발견)
    """
    # 1. 전체 순회 검사
    issues, date_count, row_count = _scan_snapshots()

    # 2. 결과 출력
    _print_summary(issues, date_count, row_count)

    # 3. 상세 CSV 저장
    csv_path = _save_detail_csv(issues)
    if csv_path is not None:
        logger.debug("상세 이슈 목록: %s", csv_path)

    # 4. 실행 이력 저장
    error_count = sum(1 for issue in issues if issue.severity is Severity.ERROR)
    warning_count = sum(1 for issue in issues if issue.severity is Severity.WARNING)
    info_count = sum(1 for issue in issues if issue.severity is Severity.INFO)

    save_metadata(
        KEY_META_TYPE,
        {
            "date_count": date_count,
            "row_count": row_count,
            "error_count": error_count,
            "warning_count": warning_count,
            "info_count": info_count,
        },
    )

    # 5. 종합 판정 — 오류가 있으면 실패 코드로 알린다
    if error_count > 0:
        logger.error("품질 오류 %d건이 발견됐습니다. 상세 목록을 확인하십시오", error_count)
        return 1

    logger.debug("품질 오류 없음 (경고 %d건, 정보 %d건)", warning_count, info_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
