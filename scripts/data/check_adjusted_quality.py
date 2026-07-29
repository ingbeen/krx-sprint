#!/usr/bin/env python3
"""2단 수정주가 정합성 검증 리포트

저장된 수정주가 시계열을 1단 원본 스냅샷과 대조해 스펙 §8의 "2단 정합성"을 검사한다.
KRX에 요청하지 않고 저장된 parquet만 읽으며, 데이터를 수정하지 않는다.

전량을 메모리에 올리지 않도록 세 번의 스트리밍 순회로 나눈다.
1단 순회(상장주식수 급변 수집) → 2단 종목별 순회(구조·가격·액션 연속성) →
최신일 대조에 필요한 1단 스냅샷만 선별 로드.

실행 방법은 docs/COMMANDS.md 참고.
"""

import sys
from collections import defaultdict
from datetime import date, datetime

import pandas as pd

from krx_sprint.collect.adjusted_quality import (
    SeriesSummary,
    SharesJumpTracker,
    check_action_continuity,
    check_adjusted_series,
    check_collection_coverage,
    check_latest_close,
    check_listing_boundary,
    summarize_adjusted,
)
from krx_sprint.collect.adjusted_store import list_collected_tickers, load_adjusted
from krx_sprint.collect.quality import QualityIssue, Severity
from krx_sprint.collect.snapshot_store import list_collected_dates, list_first_seen_dates, load_snapshot
from krx_sprint.common_constants import CACHE_DIR, COL_CLOSE, COL_TICKER, KST
from krx_sprint.utils.cli_helpers import cli_exception_handler
from krx_sprint.utils.formatting import Align, TableLogger
from krx_sprint.utils.logger import get_logger
from krx_sprint.utils.meta_manager import save_metadata

logger = get_logger()

# meta.json 최상위 키 (scripts/CLAUDE.md 메타데이터 지원 타입)
KEY_META_TYPE = "adjusted_quality"

# 진행 상황 로그 간격
SNAPSHOT_PROGRESS_INTERVAL = 400
TICKER_PROGRESS_INTERVAL = 500

# 요약 테이블 컬럼 정의
SUMMARY_TABLE_COLUMNS = [
    ("심각도", 10, Align.LEFT),
    ("검사 항목", 20, Align.LEFT),
    ("건수", 10, Align.RIGHT),
]

# 상세 CSV 컬럼
CSV_COLUMNS = ["severity", "category", "target", "detail"]


def _track_shares_jumps(snapshot_dates: list[date]) -> SharesJumpTracker:
    """1단을 순회하며 상장주식수 급변 일자를 모은다.

    Args:
        snapshot_dates: 1단 수집 일자 (오름차순)

    Returns:
        급변 일자를 담은 추적기
    """
    tracker = SharesJumpTracker()

    for index, target in enumerate(snapshot_dates, start=1):
        tracker.observe(target, load_snapshot(target))
        if index % SNAPSHOT_PROGRESS_INTERVAL == 0:
            logger.debug("1단 순회: %d/%d일 (%s)", index, len(snapshot_dates), target)

    return tracker


def _scan_adjusted(
    tickers: list[str],
    snapshot_dates: set[date],
    tracker: SharesJumpTracker,
    first_seen: dict[str, date],
) -> tuple[list[QualityIssue], list[SeriesSummary], int]:
    """2단 시계열을 종목별로 훑어 이슈와 요약을 모은다.

    Args:
        tickers: 검사 대상 티커 (오름차순)
        snapshot_dates: 1단 수집 일자 집합
        tracker: 상장주식수 급변 추적기
        first_seen: 티커 → 1단 최초 등장일

    Returns:
        (이슈 목록, 시계열 요약 목록, 누적 행 수)
    """
    issues: list[QualityIssue] = []
    summaries: list[SeriesSummary] = []
    row_count = 0

    for index, ticker in enumerate(tickers, start=1):
        frame = load_adjusted(ticker)
        row_count += len(frame)

        series_issues = check_adjusted_series(frame, ticker, snapshot_dates)
        issues.extend(series_issues)
        issues.extend(check_action_continuity(frame, ticker, tracker.observations_for(ticker)))
        issues.extend(check_listing_boundary(frame, ticker, first_seen.get(ticker)))

        # 빈 시계열은 요약할 수 없다 (이미 오류로 보고됐다)
        if not frame.empty:
            summaries.append(summarize_adjusted(frame, ticker))

        if index % TICKER_PROGRESS_INTERVAL == 0:
            logger.debug("2단 순회: %d/%d종목 (%s)", index, len(tickers), ticker)

    return issues, summaries, row_count


def _compare_latest_closes(summaries: list[SeriesSummary]) -> list[QualityIssue]:
    """각 시계열의 마지막 일자 종가를 1단 원본 종가와 대조한다.

    종목마다 마지막 일자가 다르므로(폐지 종목) 일자별로 묶어 스냅샷을 한 번씩만 읽는다.

    Args:
        summaries: 시계열 요약 목록

    Returns:
        불일치 이슈 목록
    """
    by_date: dict[date, list[SeriesSummary]] = defaultdict(list)
    for summary in summaries:
        by_date[summary.last_date].append(summary)

    issues: list[QualityIssue] = []
    for target, targets in sorted(by_date.items()):
        snapshot = load_snapshot(target)
        closes = dict(zip(snapshot[COL_TICKER], snapshot[COL_CLOSE], strict=True))

        for summary in targets:
            raw_close = closes.get(summary.ticker)
            issues.extend(check_latest_close(summary, None if raw_close is None else int(raw_close)))

    logger.debug("최신일 대조: %d종목 / 기준 일자 %d개", len(summaries), len(by_date))
    return issues


def _save_detail_csv(issues: list[QualityIssue]) -> str | None:
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
    path = CACHE_DIR / f"adjusted_quality_{datetime.now(KST).strftime('%Y%m%d_%H%M')}.csv"
    frame.to_csv(path, index=False, encoding="utf-8")

    return str(path)


def _print_summary(issues: list[QualityIssue], ticker_count: int, row_count: int) -> None:
    """검사 결과를 표로 출력한다."""
    counts: dict[tuple[str, str], int] = {}
    for issue in issues:
        key = (issue.severity.value, issue.category)
        counts[key] = counts.get(key, 0) + 1

    rows = [[severity, category, count] for (severity, category), count in sorted(counts.items())]
    if not rows:
        rows = [["-", "이슈 없음", 0]]

    table = TableLogger(SUMMARY_TABLE_COLUMNS, logger)
    table.print_table(rows, title=f"2단 정합성 검사 결과 ({ticker_count}종목 / {row_count:,}행)")

    for issue in issues:
        if issue.severity is Severity.ERROR:
            logger.warning("[%s] %s: %s", issue.category, issue.target, issue.detail)


@cli_exception_handler
def main() -> int:
    """2단 전체를 검사하고 결과를 요약한다.

    Returns:
        종료 코드 (0=오류 없음, 1=오류 발견)
    """
    # 1. 검사 전제 확인
    snapshot_dates = sorted(list_collected_dates())
    if not snapshot_dates:
        raise ValueError("수집된 1단 스냅샷이 없습니다. 먼저 collect_snapshots.py를 실행하십시오")

    collected = list_collected_tickers()
    if not collected:
        raise ValueError("수집된 2단 시계열이 없습니다. 먼저 collect_adjusted.py를 실행하십시오")

    # 2. 1단 순회 — 최초 등장일(유니버스 겸용)과 상장주식수 급변일 관측치
    first_seen = list_first_seen_dates()
    universe = set(first_seen)
    tracker = _track_shares_jumps(snapshot_dates)

    # 3. 수집 결손 + 2단 종목별 검사
    issues: list[QualityIssue] = list(check_collection_coverage(universe, collected))
    series_issues, summaries, row_count = _scan_adjusted(sorted(collected), set(snapshot_dates), tracker, first_seen)
    issues.extend(series_issues)

    # 4. 최신일 종가 대조
    issues.extend(_compare_latest_closes(summaries))

    # 5. 결과 출력
    _print_summary(issues, len(collected), row_count)

    csv_path = _save_detail_csv(issues)
    if csv_path is not None:
        logger.debug("상세 이슈 목록: %s", csv_path)

    # 6. 실행 이력 저장
    error_count = sum(1 for issue in issues if issue.severity is Severity.ERROR)
    warning_count = sum(1 for issue in issues if issue.severity is Severity.WARNING)
    info_count = sum(1 for issue in issues if issue.severity is Severity.INFO)

    save_metadata(
        KEY_META_TYPE,
        {
            "universe_count": len(universe),
            "ticker_count": len(collected),
            "row_count": row_count,
            "error_count": error_count,
            "warning_count": warning_count,
            "info_count": info_count,
        },
    )

    # 7. 종합 판정 — 오류가 있으면 실패 코드로 알린다
    if error_count > 0:
        logger.error("정합성 오류 %d건이 발견됐습니다. 상세 목록을 확인하십시오", error_count)
        return 1

    logger.debug("정합성 오류 없음 (경고 %d건, 정보 %d건)", warning_count, info_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
