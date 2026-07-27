#!/usr/bin/env python3
"""pykrx 검증 게이트 스팟체크

수집기 착수 전에 pykrx의 실제 동작을 실측한다 (스펙 §3.3 게이트 1·2·3 + §0 부가 실측).
KRX 서버에 실제 요청을 보내므로 사용자만 직접 실행한다.

실행 방법은 docs/COMMANDS.md 참고.
"""

import os
import sys
import time
from datetime import datetime
from importlib.metadata import version
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pykrx import stock

from krx_sprint.collect.gate_checks import (
    DELISTED_SAMPLE_FROM_DATE,
    DELISTED_SAMPLE_MARKET,
    DELISTED_SAMPLE_SNAPSHOT_DATE,
    DELISTED_SAMPLE_TICKER,
    DELISTED_SAMPLE_TO_DATE,
    GATE_REQUEST_DELAY_SECONDS,
    MARKET_COMPARE_DATE,
    MarketCoverage,
    SeriesAvailability,
    TickerPresence,
    check_series_availability,
    check_ticker_presence,
    compare_market_coverage,
)
from krx_sprint.utils.cli_helpers import cli_exception_handler
from krx_sprint.utils.formatting import Align, TableLogger
from krx_sprint.utils.logger import get_logger
from krx_sprint.utils.meta_manager import save_metadata

logger = get_logger()

# meta.json 최상위 키 (scripts/CLAUDE.md 메타데이터 지원 타입)
KEY_META_TYPE = "pykrx_gate"

# KRX 데이터포털 로그인 환경 변수
# pykrx는 모든 KRX 조회를 인증 세션으로 보내므로, 미설정 시 응답 본문이 비어 조회가 실패한다
ENV_KRX_ID = "KRX_ID"
ENV_KRX_PW = "KRX_PW"

# 결과 요약 테이블 컬럼 정의
GATE_TABLE_COLUMNS = [
    ("게이트", 10, Align.LEFT),
    ("항목", 30, Align.LEFT),
    ("결과", 8, Align.LEFT),
    ("근거", 46, Align.LEFT),
]

DISPLAY_PASS = "통과"
DISPLAY_FAIL = "실패"
DISPLAY_OBSERVED = "관측"


def _require_krx_credentials() -> None:
    """.env를 읽어 KRX 로그인 환경 변수가 설정됐는지 확인한다.

    미설정 상태로 조회하면 pykrx 내부에서 빈 응답을 파싱하다 실패하므로,
    원인을 알기 어려운 예외 대신 실행 전에 중단한다.

    pykrx는 import 시점에 세션을 만들지만, 세션이 없으면 첫 요청 시
    환경 변수를 다시 읽어 재로그인하므로 여기서 로드해도 인증에 반영된다.

    Raises:
        ValueError: KRX_ID 또는 KRX_PW가 비어 있는 경우
    """
    load_dotenv()

    missing = [name for name in (ENV_KRX_ID, ENV_KRX_PW) if not os.environ.get(name)]
    if missing:
        raise ValueError(
            f"KRX 로그인 자격증명이 없습니다: {', '.join(missing)}. "
            "프로젝트 루트의 .env에 값을 설정하거나 환경 변수로 export 하십시오 (docs/COMMANDS.md 참고)"
        )


def _wait() -> None:
    """KRX 레이트리밋 회피를 위해 요청 사이에 지연을 둔다."""
    time.sleep(GATE_REQUEST_DELAY_SECONDS)


def _run_gate_1() -> TickerPresence:
    """게이트 1: 폐지 종목이 1단 전종목 스냅샷에 남아 있는지 실측한다."""
    logger.debug(
        "게이트 1 조회: %s 스냅샷 (market=%s, date=%s)",
        DELISTED_SAMPLE_TICKER,
        DELISTED_SAMPLE_MARKET,
        DELISTED_SAMPLE_SNAPSHOT_DATE,
    )
    snapshot = stock.get_market_ohlcv_by_ticker(DELISTED_SAMPLE_SNAPSHOT_DATE, market=DELISTED_SAMPLE_MARKET)
    _wait()
    return check_ticker_presence(snapshot, DELISTED_SAMPLE_TICKER)


def _run_gate_2() -> tuple[SeriesAvailability, SeriesAvailability]:
    """게이트 2: 폐지 종목의 개별 시계열 조회가 수정주가/원본가 양쪽에서 되는지 실측한다."""
    logger.debug(
        "게이트 2 조회: %s 시계열 (%s~%s)",
        DELISTED_SAMPLE_TICKER,
        DELISTED_SAMPLE_FROM_DATE,
        DELISTED_SAMPLE_TO_DATE,
    )
    adjusted_df = stock.get_market_ohlcv(
        DELISTED_SAMPLE_FROM_DATE, DELISTED_SAMPLE_TO_DATE, DELISTED_SAMPLE_TICKER, adjusted=True
    )
    _wait()
    raw_df = stock.get_market_ohlcv(
        DELISTED_SAMPLE_FROM_DATE, DELISTED_SAMPLE_TO_DATE, DELISTED_SAMPLE_TICKER, adjusted=False
    )
    _wait()

    adjusted = check_series_availability(adjusted_df, DELISTED_SAMPLE_TICKER)
    raw = check_series_availability(raw_df, DELISTED_SAMPLE_TICKER)
    return adjusted, raw


def _run_gate_3() -> MarketCoverage:
    """게이트 3: market="ALL" 조회에 코넥스가 섞이는지 실측한다."""
    logger.debug("게이트 3 조회: 시장별 티커 목록 (date=%s)", MARKET_COMPARE_DATE)
    all_tickers = stock.get_market_ticker_list(MARKET_COMPARE_DATE, market="ALL")
    _wait()
    kospi_tickers = stock.get_market_ticker_list(MARKET_COMPARE_DATE, market="KOSPI")
    _wait()
    kosdaq_tickers = stock.get_market_ticker_list(MARKET_COMPARE_DATE, market="KOSDAQ")
    _wait()
    konex_tickers = stock.get_market_ticker_list(MARKET_COMPARE_DATE, market="KONEX")
    _wait()

    return compare_market_coverage(all_tickers, kospi_tickers, kosdaq_tickers, konex_tickers)


def _observe_halted_pattern() -> tuple[int, int]:
    """거래정지 의심 종목(거래량 0)의 시가 0 여부를 관측한다 (스펙 §8 판정 규칙의 근거).

    Returns:
        (거래량 0 종목 수, 그중 시가가 0인 종목 수)
    """
    logger.debug("부가 실측: 거래정지 패턴 관측 (date=%s)", MARKET_COMPARE_DATE)
    snapshot = stock.get_market_ohlcv_by_ticker(MARKET_COMPARE_DATE, market="KOSPI")
    _wait()

    if snapshot.empty:
        raise ValueError(f"{MARKET_COMPARE_DATE} 스냅샷이 비었습니다. 휴장일이면 MARKET_COMPARE_DATE를 영업일로 조정하십시오")

    zero_volume = snapshot[snapshot["거래량"] == 0]
    zero_open = zero_volume[zero_volume["시가"] == 0]
    return len(zero_volume), len(zero_open)


def _observe_today_snapshot() -> tuple[str, int]:
    """실행 시각 기준 당일 스냅샷이 조회되는지 관측한다 (당일 데이터 확정 시각 판단용).

    Returns:
        (실행 시각 KST, 당일 스냅샷 종목 수)
    """
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    today = now_kst.strftime("%Y%m%d")
    logger.debug("부가 실측: 당일 스냅샷 조회 (date=%s)", today)
    snapshot = stock.get_market_ohlcv_by_ticker(today, market="KOSPI")
    _wait()
    return now_kst.strftime("%Y-%m-%d %H:%M"), len(snapshot)


def _print_summary(
    gate1: TickerPresence,
    gate2_adjusted: SeriesAvailability,
    gate2_raw: SeriesAvailability,
    gate3: MarketCoverage,
    halted_counts: tuple[int, int],
    today_observation: tuple[str, int],
) -> None:
    """게이트 결과를 표로 출력한다."""
    zero_volume_count, zero_open_count = halted_counts
    executed_at, today_count = today_observation

    rows = [
        [
            "게이트 1",
            f"{DELISTED_SAMPLE_TICKER} 스냅샷 보존",
            DISPLAY_PASS if gate1.present else DISPLAY_FAIL,
            f"{DELISTED_SAMPLE_SNAPSHOT_DATE} 전종목 {gate1.total_count}건 중 존재={gate1.present}",
        ],
        [
            "게이트 2",
            "개별 조회 (adjusted=True)",
            DISPLAY_PASS if gate2_adjusted.available else DISPLAY_FAIL,
            f"{gate2_adjusted.row_count}행 ({gate2_adjusted.first_date}~{gate2_adjusted.last_date})",
        ],
        [
            "게이트 2",
            "개별 조회 (adjusted=False)",
            DISPLAY_PASS if gate2_raw.available else DISPLAY_FAIL,
            f"{gate2_raw.row_count}행 ({gate2_raw.first_date}~{gate2_raw.last_date})",
        ],
        [
            "게이트 3",
            "ALL 코넥스 포함 여부",
            DISPLAY_OBSERVED,
            f"ALL={gate3.all_count} KOSPI={gate3.kospi_count} KOSDAQ={gate3.kosdaq_count} KONEX={gate3.konex_count}",
        ],
        [
            "게이트 3",
            "ALL 초과 종목",
            DISPLAY_OBSERVED,
            f"초과={len(gate3.extra_tickers)} (그중 KONEX={len(gate3.konex_in_extra)}) 누락={len(gate3.missing_tickers)}",
        ],
        [
            "부가",
            "pykrx 버전",
            DISPLAY_OBSERVED,
            version("pykrx"),
        ],
        [
            "부가",
            "거래정지 패턴 (거래량 0)",
            DISPLAY_OBSERVED,
            f"{MARKET_COMPARE_DATE} 거래량 0={zero_volume_count}종목, 그중 시가 0={zero_open_count}종목",
        ],
        [
            "부가",
            "당일 스냅샷 확정 여부",
            DISPLAY_OBSERVED,
            f"실행 {executed_at} KST 기준 당일 조회 {today_count}종목",
        ],
    ]

    table = TableLogger(GATE_TABLE_COLUMNS, logger)
    table.print_table(rows, title="pykrx 검증 게이트 실측 결과 (스펙 §3.3)")

    if gate3.extra_tickers:
        logger.debug("ALL 초과 티커 샘플(최대 10개): %s", ", ".join(gate3.extra_tickers[:10]))
    if gate3.missing_tickers:
        logger.warning("ALL에 누락된 티커가 있습니다(최대 10개): %s", ", ".join(gate3.missing_tickers[:10]))


@cli_exception_handler
def main() -> int:
    """게이트 1·2·3과 부가 실측을 순차 실행하고 결과를 요약한다.

    Returns:
        종료 코드 (0=게이트 통과, 1=게이트 실패)
    """
    # 1. 실행 전제 확인
    _require_krx_credentials()

    # 2. 게이트 실측
    gate1 = _run_gate_1()
    gate2_adjusted, gate2_raw = _run_gate_2()
    gate3 = _run_gate_3()

    # 3. 부가 실측
    halted_counts = _observe_halted_pattern()
    today_observation = _observe_today_snapshot()

    # 4. 결과 출력
    _print_summary(gate1, gate2_adjusted, gate2_raw, gate3, halted_counts, today_observation)

    # 5. 실행 이력 저장
    metadata: dict[str, Any] = {
        "pykrx_version": version("pykrx"),
        "gate1_present": gate1.present,
        "gate1_total_count": gate1.total_count,
        "gate2_adjusted_rows": gate2_adjusted.row_count,
        "gate2_raw_rows": gate2_raw.row_count,
        "gate3_all_count": gate3.all_count,
        "gate3_kospi_count": gate3.kospi_count,
        "gate3_kosdaq_count": gate3.kosdaq_count,
        "gate3_konex_count": gate3.konex_count,
        "gate3_extra_count": len(gate3.extra_tickers),
        "gate3_konex_in_extra_count": len(gate3.konex_in_extra),
        "gate3_missing_count": len(gate3.missing_tickers),
        "today_snapshot_count": today_observation[1],
    }
    save_metadata(KEY_META_TYPE, metadata)

    # 6. 종합 판정 — 생존편향 방지의 전제인 게이트 1·2(수정주가)가 핵심
    blocking_failures: list[str] = []
    if not gate1.present:
        blocking_failures.append("게이트 1 (1단 스냅샷에 폐지 종목 없음)")
    if not gate2_adjusted.available:
        blocking_failures.append("게이트 2 (2단 수정주가 개별 조회 불가)")

    if blocking_failures:
        logger.error("게이트 실패: %s — 스펙 §3.3 대응 서브루틴 검토가 필요합니다", " / ".join(blocking_failures))
        return 1

    logger.debug("게이트 1·2 통과. 게이트 3 결과는 스펙 §0에 기록하십시오")
    return 0


if __name__ == "__main__":
    sys.exit(main())
