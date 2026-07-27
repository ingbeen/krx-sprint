#!/usr/bin/env python3
"""1단 전종목 스냅샷 백필 수집

누락된 영업일의 KOSPI·KOSDAQ 전종목 스냅샷을 수집해 일자별 parquet으로 저장한다
(스펙 §4 1단, §7.2 저장 구조, §7.3 증분 로직).

KRX 서버에 실제 요청을 보내므로 사용자만 직접 실행한다.
수집 범위는 명령행 인자가 아니라 상수로 관리한다 (scripts/CLAUDE.md).

실행 방법은 docs/COMMANDS.md 참고.
"""

import argparse
import sys
from datetime import date, datetime

from krx_sprint.collect.backfill import BackfillPaths, BackfillResult, backfill
from krx_sprint.collect.calendar import list_target_dates, resolve_last_collectable_date
from krx_sprint.collect.krx_credentials import load_krx_credentials
from krx_sprint.collect.meta_store import load_holidays
from krx_sprint.collect.names import merge_names
from krx_sprint.collect.snapshot_store import list_collected_dates
from krx_sprint.common_constants import COLLECTION_START_DATE, KST, MARKETS
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
KEY_META_TYPE = "snapshot_backfill"

# 결과 요약 테이블 컬럼 정의
SUMMARY_TABLE_COLUMNS = [
    ("항목", 20, Align.LEFT),
    ("값", 44, Align.LEFT),
]


def _parse_args() -> argparse.Namespace:
    """명령행 인자를 파싱한다.

    수집 기간 전체를 한 번에 돌리면 두 시간 이상 걸리므로, 실행 단위를 나눌 수 있도록
    일자 수 상한만 옵션으로 둔다 (scripts/CLAUDE.md의 "수집 기간 지정" 예외).
    """
    parser = argparse.ArgumentParser(description="1단 전종목 스냅샷 백필 수집")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="이번 실행에서 수집할 최대 일자 수 (미지정 시 누락 일자 전체)",
    )
    return parser.parse_args()


def _collect_latest_names(target: date) -> int:
    """지정 일자 기준 티커 → 종목명 매핑을 names.csv에 병합한다.

    업종별 분류 현황은 시장당 1회 호출로 종목명을 반환한다.
    휴장일에는 빈 결과가 오므로 수집에 성공한 영업일을 넘겨받는다.

    Args:
        target: 조회 기준 일자 (수집에 성공한 영업일)

    Returns:
        병합한 종목 수
    """
    names: dict[str, str] = {}
    for market in MARKETS:
        classifications = stock.get_market_sector_classifications(target.strftime("%Y%m%d"), market)
        if classifications.empty:
            logger.warning("종목명 조회 결과가 비었습니다 (market=%s)", market)
            continue
        names.update({str(ticker): str(name) for ticker, name in classifications["종목명"].items()})

    if not names:
        logger.warning("종목명을 수집하지 못해 names.csv를 갱신하지 않습니다")
        return 0

    merge_names(names)
    return len(names)


def _print_summary(result: BackfillResult, target_count: int, name_count: int) -> None:
    """수집 결과를 표로 출력한다."""
    rows = [
        ["수집 대상 일자", f"{target_count}일"],
        ["신규 수집", f"{len(result.collected)}일"],
        ["휴장 판정", f"{len(result.holidays)}일"],
        ["수집 실패", f"{len(result.failures)}일"],
        ["종목명 갱신", f"{name_count}종목"],
    ]

    table = TableLogger(SUMMARY_TABLE_COLUMNS, logger)
    table.print_table(rows, title="1단 스냅샷 수집 결과")

    if result.failures:
        failed = ", ".join(target.isoformat() for target in result.failures[:10])
        logger.warning("실패 일자(최대 10개): %s — 재실행하면 failures.json 기준으로 다시 시도합니다", failed)


@cli_exception_handler
def main() -> int:
    """누락 일자를 수집하고 결과를 요약한다.

    Returns:
        종료 코드 (0=성공, 1=실패 일자 존재)
    """
    args = _parse_args()

    # 1. 실행 전제 확인 (자격증명은 모듈 로드 시점에 이미 검증됨)
    if args.limit is not None and args.limit < 1:
        raise ValueError(f"--limit은 1 이상이어야 합니다: {args.limit}")

    # 2. 수집 대상 일자 산출 (당일은 확정 시각 이후에만 포함)
    paths = BackfillPaths.default()
    last_date = resolve_last_collectable_date(datetime.now(KST))
    targets = list_target_dates(
        COLLECTION_START_DATE,
        last_date,
        collected=list_collected_dates(base_dir=paths.snapshots_dir),
        holidays=load_holidays(paths.holidays_path),
    )
    logger.debug("누락 일자: %d일 (%s ~ %s)", len(targets), COLLECTION_START_DATE, last_date)

    if args.limit is not None:
        targets = targets[: args.limit]
        logger.debug("이번 실행 대상: %d일 (--limit %d)", len(targets), args.limit)

    if not targets:
        logger.debug("수집할 일자가 없습니다")
        return 0

    # 3. 백필 실행
    result = backfill(targets, stock.get_market_ohlcv_by_ticker, stock.get_market_cap_by_ticker, paths)

    # 4. 종목명 갱신 (수집에 성공한 최근 영업일 기준 1회)
    name_count = _collect_latest_names(result.collected[-1]) if result.collected else 0

    # 5. 결과 출력
    _print_summary(result, len(targets), name_count)

    # 6. 실행 이력 저장
    save_metadata(
        KEY_META_TYPE,
        {
            "start_date": COLLECTION_START_DATE.isoformat(),
            "last_date": last_date.isoformat(),
            "target_count": len(targets),
            "collected_count": len(result.collected),
            "holiday_count": len(result.holidays),
            "failure_count": len(result.failures),
            "name_count": name_count,
        },
    )

    if result.failures:
        logger.error("수집에 실패한 일자가 있습니다: %d일", len(result.failures))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
