"""gate_checks 테스트

검증 게이트(스펙 §3.3)의 판정 규칙을 pykrx 호출 없이 고정한다.
"""

import pandas as pd
import pytest

from krx_sprint.collect.gate_checks import (
    check_series_availability,
    check_ticker_presence,
    compare_market_coverage,
)

# 게이트 1·2 검증에 사용하는 폐지 종목 표본 (한진해운)
TICKER = "117930"


def _snapshot(tickers: list[str]) -> pd.DataFrame:
    """티커를 인덱스로 하는 최소 스냅샷 DataFrame을 만든다."""
    return pd.DataFrame({"종가": [1000] * len(tickers)}, index=pd.Index(tickers, name="티커"))


def _series(dates: list[str]) -> pd.DataFrame:
    """일자를 인덱스로 하는 최소 시계열 DataFrame을 만든다."""
    return pd.DataFrame({"종가": [1000] * len(dates)}, index=pd.DatetimeIndex(dates))


class TestCheckTickerPresence:
    """1단 스냅샷의 폐지 종목 보존 판정(게이트 1) 계약을 고정한다."""

    def test_detects_present_ticker(self):
        """
        목적: 스냅샷에 티커가 있으면 present=True와 전체 종목 수를 함께 반환한다.

        Given: 대상 티커를 포함한 3종목 스냅샷
        When: check_ticker_presence 호출
        Then: present=True, total_count=3
        """
        # Given
        snapshot = _snapshot(["005930", TICKER, "000660"])

        # When
        result = check_ticker_presence(snapshot, TICKER)

        # Then
        assert result.present is True
        assert result.total_count == 3

    def test_detects_absent_ticker(self):
        """
        목적: 스냅샷에 티커가 없으면 present=False를 반환한다 (예외 아님).

        Given: 대상 티커가 빠진 스냅샷
        When: check_ticker_presence 호출
        Then: present=False — 게이트 실패는 판정 결과로 표현된다
        """
        # Given
        snapshot = _snapshot(["005930", "000660"])

        # When
        result = check_ticker_presence(snapshot, TICKER)

        # Then
        assert result.present is False

    def test_rejects_empty_snapshot(self):
        """
        목적: 빈 스냅샷을 "종목 없음"으로 조용히 판정하지 않는다 (경계 조건).

        Given: 빈 DataFrame (휴장 또는 조회 실패)
        When: check_ticker_presence 호출
        Then: ValueError가 발생한다
        """
        # Given / When / Then
        with pytest.raises(ValueError, match="비어"):
            check_ticker_presence(pd.DataFrame(), TICKER)

    def test_rejects_non_string_index(self):
        """
        목적: 티커 인덱스가 문자열이 아니면 즉시 예외를 발생시킨다 (선행 0 손실 감지).

        Given: 정수 인덱스를 가진 스냅샷
        When: check_ticker_presence 호출
        Then: ValueError가 발생한다
        """
        # Given
        snapshot = pd.DataFrame({"종가": [1000]}, index=pd.Index([117930]))

        # When / Then
        with pytest.raises(ValueError, match="문자열"):
            check_ticker_presence(snapshot, TICKER)

    def test_rejects_malformed_ticker(self):
        """
        목적: 6자리 숫자 문자열이 아닌 티커는 입력 단계에서 거부한다.

        Given: 자릿수가 부족한 티커
        When: check_ticker_presence 호출
        Then: ValueError가 발생한다
        """
        # Given
        snapshot = _snapshot(["005930"])

        # When / Then
        with pytest.raises(ValueError, match="6자리"):
            check_ticker_presence(snapshot, "5930")


class TestCheckSeriesAvailability:
    """2단 개별 조회의 폐지 종목 지원 판정(게이트 2) 계약을 고정한다."""

    def test_reports_available_range(self):
        """
        목적: 시계열이 반환되면 행 수와 조회 구간을 함께 보고한다.

        Given: 3거래일치 시계열
        When: check_series_availability 호출
        Then: available=True, row_count=3, 시작/종료 일자가 채워진다
        """
        # Given
        series = _series(["2016-01-04", "2016-01-05", "2016-01-06"])

        # When
        result = check_series_availability(series, TICKER)

        # Then
        assert result.available is True
        assert result.row_count == 3
        assert result.first_date == "2016-01-04"
        assert result.last_date == "2016-01-06"

    def test_empty_series_is_unavailable(self):
        """
        목적: 빈 시계열은 예외가 아니라 "조회 불가" 판정으로 표현한다 (게이트 2의 답 자체).

        Given: 빈 DataFrame
        When: check_series_availability 호출
        Then: available=False, row_count=0, 일자는 None
        """
        # Given / When
        result = check_series_availability(pd.DataFrame(), TICKER)

        # Then
        assert result.available is False
        assert result.row_count == 0
        assert result.first_date is None
        assert result.last_date is None

    def test_rejects_non_datetime_index(self):
        """
        목적: 시계열 인덱스가 DatetimeIndex가 아니면 즉시 예외를 발생시킨다.

        Given: 문자열 인덱스를 가진 시계열
        When: check_series_availability 호출
        Then: ValueError가 발생한다
        """
        # Given
        series = pd.DataFrame({"종가": [1000]}, index=pd.Index(["2016-01-04"]))

        # When / Then
        with pytest.raises(ValueError, match="DatetimeIndex"):
            check_series_availability(series, TICKER)

    def test_rejects_malformed_ticker(self):
        """
        목적: 티커 형식 검증은 시계열 판정에도 동일하게 적용된다.

        Given: 숫자가 아닌 문자가 섞인 티커
        When: check_series_availability 호출
        Then: ValueError가 발생한다
        """
        # Given
        series = _series(["2016-01-04"])

        # When / Then
        with pytest.raises(ValueError, match="6자리"):
            check_series_availability(series, "11793A")


class TestCompareMarketCoverage:
    """market="ALL"의 코넥스 포함 여부 판정(게이트 3) 계약을 고정한다."""

    def test_no_extra_when_all_equals_union(self):
        """
        목적: ALL이 KOSPI∪KOSDAQ와 같으면 초과 종목이 없다고 판정한다.

        Given: ALL = KOSPI + KOSDAQ
        When: compare_market_coverage 호출
        Then: includes_extra_market=False, 초과/누락 티커 모두 없음
        """
        # Given / When
        result = compare_market_coverage(
            all_tickers=["005930", "000660", "035720"],
            kospi_tickers=["005930", "000660"],
            kosdaq_tickers=["035720"],
            konex_tickers=["900110"],
        )

        # Then
        assert result.includes_extra_market is False
        assert result.extra_tickers == ()
        assert result.missing_tickers == ()

    def test_detects_konex_tickers_in_all(self):
        """
        목적: ALL에 코넥스 종목이 섞이면 초과 종목으로 잡아내고 코넥스임을 식별한다.

        Given: ALL에 코넥스 티커가 포함됨
        When: compare_market_coverage 호출
        Then: extra_tickers에 잡히고 konex_in_extra와 일치한다
        """
        # Given / When
        result = compare_market_coverage(
            all_tickers=["005930", "035720", "900110"],
            kospi_tickers=["005930"],
            kosdaq_tickers=["035720"],
            konex_tickers=["900110"],
        )

        # Then
        assert result.includes_extra_market is True
        assert result.extra_tickers == ("900110",)
        assert result.konex_in_extra == ("900110",)

    def test_detects_missing_tickers(self):
        """
        목적: KOSPI/KOSDAQ에 있는데 ALL에 없는 종목도 비정상으로 보고한다.

        Given: ALL에서 KOSDAQ 종목 하나가 빠짐
        When: compare_market_coverage 호출
        Then: missing_tickers에 해당 티커가 담긴다
        """
        # Given / When
        result = compare_market_coverage(
            all_tickers=["005930"],
            kospi_tickers=["005930"],
            kosdaq_tickers=["035720"],
            konex_tickers=["900110"],
        )

        # Then
        assert result.missing_tickers == ("035720",)

    def test_extra_tickers_are_sorted(self):
        """
        목적: 집합 연산 결과의 순서를 정렬로 고정한다 (결정적 테스트).

        Given: 입력 순서가 뒤섞인 초과 티커 2개
        When: compare_market_coverage 호출
        Then: extra_tickers가 오름차순으로 반환된다
        """
        # Given / When
        result = compare_market_coverage(
            all_tickers=["900200", "005930", "900110"],
            kospi_tickers=["005930"],
            kosdaq_tickers=["005930"],
            konex_tickers=["900110", "900200"],
        )

        # Then
        assert result.extra_tickers == ("900110", "900200")

    def test_rejects_empty_input(self):
        """
        목적: 빈 조회 결과를 "차이 없음"으로 조용히 판정하지 않는다 (경계 조건).

        Given: 빈 ALL 티커 리스트
        When: compare_market_coverage 호출
        Then: ValueError가 발생한다
        """
        # Given / When / Then
        with pytest.raises(ValueError, match="비어"):
            compare_market_coverage(
                all_tickers=[],
                kospi_tickers=["005930"],
                kosdaq_tickers=["035720"],
                konex_tickers=["900110"],
            )
