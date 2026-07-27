"""검증 게이트 판정 규칙

수집기 착수 전 pykrx의 실제 동작을 확정하기 위한 판정 로직을 담당한다.
판정 근거는 docs/데이터수집_스펙_v2.md §3.3 참고.

이 모듈은 pykrx를 import 하지 않는다. 조회는 CLI 계층이 수행하고,
이 모듈은 조회 결과(DataFrame·티커 목록)만 입력으로 받아 판정한다.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

# 티커 형식 (6자리 숫자 문자열, 선행 0 보존)
TICKER_LENGTH = 6

# 게이트 1·2 검증용 폐지 종목 표본 (한진해운, 유가증권시장에서 상장폐지)
DELISTED_SAMPLE_TICKER = "117930"
DELISTED_SAMPLE_MARKET = "KOSPI"
DELISTED_SAMPLE_SNAPSHOT_DATE = "20160601"  # 폐지 전 거래일
DELISTED_SAMPLE_FROM_DATE = "20160101"
DELISTED_SAMPLE_TO_DATE = "20161231"

# 게이트 3 시장 비교 기준 일자 (수집 기간 내 영업일)
MARKET_COMPARE_DATE = "20240102"

# 게이트 스팟체크의 요청 간 지연 (초, 스펙 §9 레이트리밋)
GATE_REQUEST_DELAY_SECONDS = 1.0


@dataclass(frozen=True)
class TickerPresence:
    """일별 전종목 스냅샷에 특정 티커가 존재하는지에 대한 판정 결과

    Attributes:
        ticker: 판정 대상 티커
        present: 스냅샷에 존재하는지 여부
        total_count: 스냅샷의 전체 종목 수 (판정 근거)
    """

    ticker: str
    present: bool
    total_count: int


@dataclass(frozen=True)
class SeriesAvailability:
    """종목별 시계열 조회가 데이터를 반환했는지에 대한 판정 결과

    Attributes:
        ticker: 판정 대상 티커
        row_count: 반환된 행 수
        first_date: 조회 구간의 시작 일자 (YYYY-MM-DD, 데이터 없으면 None)
        last_date: 조회 구간의 종료 일자 (YYYY-MM-DD, 데이터 없으면 None)
    """

    ticker: str
    row_count: int
    first_date: str | None
    last_date: str | None

    @property
    def available(self) -> bool:
        """조회 결과가 존재하는지 여부"""
        return self.row_count > 0


@dataclass(frozen=True)
class MarketCoverage:
    """market="ALL" 조회가 KOSPI∪KOSDAQ 밖의 종목을 포함하는지에 대한 판정 결과

    Attributes:
        all_count: ALL 조회 종목 수
        kospi_count: KOSPI 조회 종목 수
        kosdaq_count: KOSDAQ 조회 종목 수
        konex_count: KONEX 조회 종목 수
        extra_tickers: ALL에만 있고 KOSPI·KOSDAQ에는 없는 티커 (오름차순)
        missing_tickers: KOSPI·KOSDAQ에 있는데 ALL에는 없는 티커 (오름차순)
        konex_in_extra: 초과 티커 중 KONEX 목록에도 있는 티커 (오름차순)
    """

    all_count: int
    kospi_count: int
    kosdaq_count: int
    konex_count: int
    extra_tickers: tuple[str, ...]
    missing_tickers: tuple[str, ...]
    konex_in_extra: tuple[str, ...]

    @property
    def includes_extra_market(self) -> bool:
        """ALL 조회에 KOSPI·KOSDAQ 밖의 종목이 섞여 있는지 여부"""
        return len(self.extra_tickers) > 0


def _validate_ticker(ticker: str) -> None:
    """티커 형식을 검증한다.

    Args:
        ticker: 검증할 티커

    Raises:
        ValueError: 6자리 숫자 문자열이 아닌 경우
    """
    if len(ticker) != TICKER_LENGTH or not ticker.isdigit():
        raise ValueError(f"티커는 {TICKER_LENGTH}자리 숫자 문자열이어야 합니다: {ticker!r}")


def check_ticker_presence(snapshot: pd.DataFrame, ticker: str) -> TickerPresence:
    """일별 전종목 스냅샷에 대상 티커가 존재하는지 판정한다 (스펙 §3.3 게이트 1).

    빈 스냅샷은 "종목 없음"이 아니라 조회 실패·휴장으로 보고 예외를 발생시킨다.
    두 상황을 섞으면 생존편향 검증 결과가 왜곡된다.

    Args:
        snapshot: 티커를 인덱스로 하는 전종목 스냅샷
        ticker: 존재 여부를 확인할 티커

    Returns:
        존재 여부와 전체 종목 수를 담은 판정 결과

    Raises:
        ValueError: 티커 형식이 잘못되었거나, 스냅샷이 비었거나, 인덱스가 문자열이 아닌 경우
    """
    # 1. 입력 검증
    _validate_ticker(ticker)

    if snapshot.empty:
        raise ValueError("스냅샷이 비어 있습니다 (휴장 또는 조회 실패와 '종목 없음'은 구분해야 합니다)")

    # 2. 티커 인덱스가 문자열인지 확인 (정수 인덱스는 선행 0이 손실된 상태)
    if snapshot.index.inferred_type != "string":
        raise ValueError(f"스냅샷 인덱스가 문자열이 아닙니다: {snapshot.index.inferred_type}")

    # 3. 존재 여부 판정
    return TickerPresence(
        ticker=ticker,
        present=ticker in snapshot.index,
        total_count=len(snapshot),
    )


def check_series_availability(series: pd.DataFrame, ticker: str) -> SeriesAvailability:
    """종목별 시계열 조회가 데이터를 반환했는지 판정한다 (스펙 §3.3 게이트 2).

    빈 결과는 게이트 2의 답 자체(폐지 종목 개별 조회 불가)이므로 예외가 아니라
    판정 결과로 표현한다.

    Args:
        series: 일자를 인덱스로 하는 종목별 시계열
        ticker: 조회한 티커

    Returns:
        행 수와 조회 구간을 담은 판정 결과

    Raises:
        ValueError: 티커 형식이 잘못되었거나, 비어 있지 않은데 인덱스가 DatetimeIndex가 아닌 경우
    """
    # 1. 입력 검증
    _validate_ticker(ticker)

    # 2. 빈 결과는 "조회 불가" 판정으로 반환
    if series.empty:
        return SeriesAvailability(ticker=ticker, row_count=0, first_date=None, last_date=None)

    # 3. 인덱스 타입 확인 (pykrx 반환 형식이 바뀌면 즉시 인지)
    if not isinstance(series.index, pd.DatetimeIndex):
        raise ValueError(f"시계열 인덱스가 DatetimeIndex가 아닙니다: {type(series.index).__name__}")

    return SeriesAvailability(
        ticker=ticker,
        row_count=len(series),
        first_date=series.index.min().strftime("%Y-%m-%d"),
        last_date=series.index.max().strftime("%Y-%m-%d"),
    )


def compare_market_coverage(
    all_tickers: Sequence[str],
    kospi_tickers: Sequence[str],
    kosdaq_tickers: Sequence[str],
    konex_tickers: Sequence[str],
) -> MarketCoverage:
    """market="ALL" 조회가 KOSPI∪KOSDAQ 밖의 종목을 포함하는지 판정한다 (스펙 §3.3 게이트 3).

    초과 종목이 KONEX 목록에도 있으면 ALL에 코넥스가 섞인 것으로 확정할 수 있다.

    Args:
        all_tickers: market="ALL" 조회 결과 티커 목록
        kospi_tickers: market="KOSPI" 조회 결과 티커 목록
        kosdaq_tickers: market="KOSDAQ" 조회 결과 티커 목록
        konex_tickers: market="KONEX" 조회 결과 티커 목록

    Returns:
        시장별 건수와 초과·누락 티커를 담은 판정 결과

    Raises:
        ValueError: 네 목록 중 하나라도 비어 있는 경우
    """
    # 1. 입력 검증 — 빈 조회 결과를 "차이 없음"으로 판정하면 코넥스 혼입을 놓친다
    for market_name, tickers in (
        ("ALL", all_tickers),
        ("KOSPI", kospi_tickers),
        ("KOSDAQ", kosdaq_tickers),
        ("KONEX", konex_tickers),
    ):
        if not tickers:
            raise ValueError(f"{market_name} 티커 목록이 비어 있습니다 (조회 실패 가능성)")

    # 2. 집합 연산으로 초과·누락 산출
    all_set = set(all_tickers)
    union_set = set(kospi_tickers) | set(kosdaq_tickers)
    extra = all_set - union_set

    return MarketCoverage(
        all_count=len(all_set),
        kospi_count=len(set(kospi_tickers)),
        kosdaq_count=len(set(kosdaq_tickers)),
        konex_count=len(set(konex_tickers)),
        extra_tickers=tuple(sorted(extra)),
        missing_tickers=tuple(sorted(union_set - all_set)),
        konex_in_extra=tuple(sorted(extra & set(konex_tickers))),
    )
