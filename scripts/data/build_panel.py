#!/usr/bin/env python3
"""백테스트 통합 패널 빌드

1단 원본가 스냅샷과 2단 수정주가를 하나의 (일자, 티커) 패널로 합쳐
`storage/cache/panel/{YYYY}.parquet`에 저장한다.

KRX에 요청하지 않고 저장된 parquet만 읽으며, 원천 데이터를 수정하지 않는다.
패널은 파생 캐시라 실행할 때마다 전량을 다시 쓴다.

실행 방법은 docs/COMMANDS.md 참고.
"""

import sys

from krx_sprint.common_constants import PANEL_DIR, PANEL_FLAG_COLUMNS
from krx_sprint.panel.build import build_panel
from krx_sprint.utils.cli_helpers import cli_exception_handler
from krx_sprint.utils.formatting import Align, TableLogger
from krx_sprint.utils.logger import get_logger
from krx_sprint.utils.meta_manager import save_metadata

logger = get_logger()

# meta.json 최상위 키 (scripts/CLAUDE.md 메타데이터 지원 타입)
KEY_META_TYPE = "panel_build"

# 요약 테이블 컬럼 정의
SUMMARY_TABLE_COLUMNS = [
    ("항목", 24, Align.LEFT),
    ("값", 20, Align.RIGHT),
]

# 플래그 테이블 컬럼 정의
FLAG_TABLE_COLUMNS = [
    ("처리 규칙 플래그", 24, Align.LEFT),
    ("건수", 12, Align.RIGHT),
    ("비율", 10, Align.RIGHT),
]


@cli_exception_handler
def main() -> int:
    """통합 패널을 빌드하고 결과를 요약한다.

    Returns:
        종료 코드 (0=성공)
    """
    # 1. 빌드
    logger.debug("통합 패널 빌드 시작")
    summary = build_panel()

    # 2. 규모 요약
    table = TableLogger(SUMMARY_TABLE_COLUMNS, logger)
    table.print_table(
        [
            ["행 수", f"{summary.row_count:,}"],
            ["종목 수", f"{summary.ticker_count:,}"],
            ["연도 파일 수", f"{summary.year_count:,}"],
            ["구간 시작", summary.first_date.isoformat()],
            ["구간 종료", summary.last_date.isoformat()],
        ],
        title=f"통합 패널 빌드 결과 ({PANEL_DIR})",
    )

    # 3. 플래그 건수 — 품질 리포트 수치와 대조하는 근거가 된다
    flag_table = TableLogger(FLAG_TABLE_COLUMNS, logger)
    flag_table.print_table(
        [
            [column, f"{summary.flag_counts[column]:,}", f"{summary.flag_counts[column] / summary.row_count:.4%}"]
            for column in PANEL_FLAG_COLUMNS
        ],
        title="처리 규칙 플래그 집계",
    )

    # 4. 실행 이력 저장
    save_metadata(
        KEY_META_TYPE,
        {
            "row_count": summary.row_count,
            "ticker_count": summary.ticker_count,
            "year_count": summary.year_count,
            "first_date": summary.first_date.isoformat(),
            "last_date": summary.last_date.isoformat(),
            **{f"flag_{column}": count for column, count in summary.flag_counts.items()},
        },
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
