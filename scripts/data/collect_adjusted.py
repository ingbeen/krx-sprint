#!/usr/bin/env python3
"""2단 종목별 수정주가 시계열 수집

1단 스냅샷 합집합의 전종목에 대해 수정주가 시계열을 수집해 종목별 parquet으로 저장한다
(스펙 §4 2단, §7.2 저장 구조).

지표 계산(이평·전저점·스윙)은 원본가로는 분할·증자 구간에서 가짜 신호를 만들기 때문에
수정주가가 필요하다. 대상은 1단 합집합이므로 폐지 종목도 포함된다 (스펙 §3.2).

KRX 서버에 실제 요청을 보내므로 사용자만 직접 실행한다.

실행 방법은 docs/COMMANDS.md 참고.
"""

import argparse
import sys
from datetime import date

import pandas as pd

from krx_sprint.collect.adjusted_backfill import AdjustedBackfillResult, backfill_adjusted
from krx_sprint.collect.adjusted_store import list_collected_tickers
from krx_sprint.collect.krx_credentials import load_krx_credentials
from krx_sprint.collect.snapshot_store import list_all_tickers, list_collected_dates
from krx_sprint.common_constants import COLLECTION_START_DATE
from krx_sprint.utils.cli_helpers import cli_exception_handler
from krx_sprint.utils.formatting import Align, TableLogger
from krx_sprint.utils.logger import get_logger
from krx_sprint.utils.meta_manager import save_metadata

# pykrx는 import 시점에 환경 변수를 읽어 인증 세션을 만든다.
# 자격증명을 먼저 로드해야 첫 조회부터 인증된 세션을 사용한다.
load_krx_credentials()

from pykrx import stock  # noqa: E402

logger = get_logger()

# meta.json 최상위 키 (scripts/CLAUDE.md 메타데이터 지원 타입)
KEY_META_TYPE = "adjusted_backfill"

# 결과 요약 테이블 컬럼 정의
SUMMARY_TABLE_COLUMNS = [
    ("항목", 20, Align.LEFT),
    ("값", 44, Align.LEFT),
]


def _parse_args() -> argparse.Namespace:
    """명령행 인자를 파싱한다.

    전종목 수집은 한 번에 한 시간 이상 걸리므로, 실행 단위를 나눌 수 있도록
    종목 수 상한만 옵션으로 둔다 (scripts/CLAUDE.md의 "수집 기간 지정" 예외).
    """
    parser = argparse.ArgumentParser(description="2단 종목별 수정주가 시계열 수집")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="이번 실행에서 수집할 최대 종목 수 (미지정 시 미수집 종목 전체)",
    )
    return parser.parse_args()


def _fetch_adjusted(from_date: str, to_date: str, ticker: str) -> pd.DataFrame:
    """수정주가 시계열을 조회한다.

    `adjusted`를 항상 명시해 pykrx 기본값이 바뀌어도 원본가가 섞이지 않게 한다 (스펙 §10.3).

    Args:
        from_date: 조회 시작일 (YYYYMMDD)
        to_date: 조회 종료일 (YYYYMMDD)
        ticker: 대상 티커

    Returns:
        일자를 인덱스로 하는 조회 결과
    """
    return stock.get_market_ohlcv(from_date, to_date, ticker, adjusted=True)


def _resolve_last_snapshot_date() -> date:
    """1단 최종 수집 일자를 구한다.

    2단 조회 종료일을 1단에 맞춘다 — 1단에 없는 날짜를 2단이 가지면
    최신일 종가 대조(스펙 §8 2단 정합성)가 불가능해진다.

    Returns:
        1단 최종 수집 일자

    Raises:
        ValueError: 수집된 스냅샷이 없는 경우
    """
    collected = list_collected_dates()
    if not collected:
        raise ValueError("수집된 1단 스냅샷이 없습니다. 먼저 collect_snapshots.py를 실행하십시오")

    return max(collected)


def _print_summary(
    result: AdjustedBackfillResult,
    universe_count: int,
    target_count: int,
    last_date: date,
) -> None:
    """수집 결과를 표로 출력한다."""
    rows = [
        ["유니버스", f"{universe_count}종목"],
        ["조회 구간", f"{COLLECTION_START_DATE} ~ {last_date}"],
        ["이번 대상", f"{target_count}종목"],
        ["신규 수집", f"{len(result.collected)}종목"],
        ["수집 실패", f"{len(result.failures)}종목"],
    ]

    table = TableLogger(SUMMARY_TABLE_COLUMNS, logger)
    table.print_table(rows, title="2단 수정주가 수집 결과")

    if result.failures:
        failed = ", ".join(result.failures[:10])
        logger.warning("실패 종목(최대 10개): %s — 재실행하면 미수집 종목만 다시 시도합니다", failed)


@cli_exception_handler
def main() -> int:
    """미수집 종목을 수집하고 결과를 요약한다.

    Returns:
        종료 코드 (0=성공, 1=실패 종목 존재)
    """
    args = _parse_args()

    # 1. 실행 전제 확인 (자격증명은 모듈 로드 시점에 이미 검증됨)
    if args.limit is not None and args.limit < 1:
        raise ValueError(f"--limit은 1 이상이어야 합니다: {args.limit}")

    # 2. 대상 종목 산출 (1단 합집합 - 이미 수집된 종목)
    last_date = _resolve_last_snapshot_date()
    universe = list_all_tickers()
    if not universe:
        raise ValueError("1단 스냅샷에서 티커를 찾지 못했습니다")

    targets = sorted(universe - list_collected_tickers())
    logger.debug("유니버스 %d종목 / 미수집 %d종목", len(universe), len(targets))

    if args.limit is not None:
        targets = targets[: args.limit]
        logger.debug("이번 실행 대상: %d종목 (--limit %d)", len(targets), args.limit)

    if not targets:
        logger.debug("수집할 종목이 없습니다")
        return 0

    # 3. 백필 실행
    result = backfill_adjusted(targets, _fetch_adjusted, COLLECTION_START_DATE, last_date)

    # 4. 결과 출력
    _print_summary(result, len(universe), len(targets), last_date)

    # 5. 실행 이력 저장
    save_metadata(
        KEY_META_TYPE,
        {
            "start_date": COLLECTION_START_DATE.isoformat(),
            "last_date": last_date.isoformat(),
            "universe_count": len(universe),
            "target_count": len(targets),
            "collected_count": len(result.collected),
            "failure_count": len(result.failures),
        },
    )

    if result.failures:
        logger.error("수집에 실패한 종목이 있습니다: %d종목", len(result.failures))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
