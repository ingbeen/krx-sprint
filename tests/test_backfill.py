"""backfill 테스트

일자별 수집 루프의 성공/휴장/실패 분기와 재시도·체크포인트 계약을 고정한다 (스펙 §7.3·§9).
조회 함수는 모두 스텁으로 주입하며 네트워크 호출은 없다.
"""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from krx_sprint.collect.backfill import BackfillPaths, backfill
from krx_sprint.collect.meta_store import load_failures, load_holidays
from krx_sprint.collect.snapshot_store import list_collected_dates

MARKETS = ("KOSPI", "KOSDAQ")


def _paths(tmp_path: Path) -> BackfillPaths:
    """tmp_path 기반 저장 경로 묶음을 만든다."""
    return BackfillPaths(
        snapshots_dir=tmp_path / "snapshots",
        holidays_path=tmp_path / "meta" / "holidays.json",
        failures_path=tmp_path / "meta" / "failures.json",
    )


def _ohlcv(tickers: list[str]) -> pd.DataFrame:
    """OHLCV 조회 결과 스텁."""
    count = len(tickers)
    return pd.DataFrame(
        {
            "시가": [1000] * count,
            "고가": [1100] * count,
            "저가": [900] * count,
            "종가": [1050] * count,
            "거래량": [500] * count,
            "거래대금": [525000] * count,
            "등락률": [1.5] * count,
        },
        index=pd.Index(tickers, name="티커"),
    )


def _cap(tickers: list[str]) -> pd.DataFrame:
    """시가총액 조회 결과 스텁."""
    count = len(tickers)
    return pd.DataFrame(
        {
            "종가": [1050] * count,
            "시가총액": [10500000] * count,
            "거래량": [500] * count,
            "거래대금": [525000] * count,
            "상장주식수": [10000] * count,
        },
        index=pd.Index(tickers, name="티커"),
    )


def _ok_fetchers() -> tuple[Callable[[str, str], pd.DataFrame], Callable[[str, str], pd.DataFrame]]:
    """항상 정상 결과를 돌려주는 조회 함수 쌍."""
    return (lambda _date, _market: _ohlcv(["005930"]), lambda _date, _market: _cap(["005930"]))


def _no_sleep(_seconds: float) -> None:
    """테스트에서 실제 대기를 제거한다."""


class TestBackfillSuccess:
    """정상 수집 경로를 고정한다."""

    def test_saves_snapshot_per_date(self, tmp_path: Path):
        """
        목적: 대상 일자마다 스냅샷 파일을 하나씩 저장한다.

        Given: 영업일 2일
        When: backfill 실행
        Then: 두 일자가 수집 완료로 기록된다
        """
        # Given
        paths = _paths(tmp_path)
        targets = [date(2019, 1, 2), date(2019, 1, 3)]
        fetch_ohlcv, fetch_cap = _ok_fetchers()

        # When
        result = backfill(targets, fetch_ohlcv, fetch_cap, paths, markets=MARKETS, sleep=_no_sleep)

        # Then
        assert result.collected == tuple(targets)
        assert list_collected_dates(base_dir=paths.snapshots_dir) == set(targets)

    def test_merges_all_markets_into_one_file(self, tmp_path: Path):
        """
        목적: KOSPI·KOSDAQ 결과를 한 일자 파일로 합친다.

        Given: 시장마다 다른 티커를 돌려주는 조회 함수
        When: backfill 실행
        Then: 저장 파일에 두 시장 종목이 모두 들어간다
        """
        # Given
        paths = _paths(tmp_path)
        market_tickers = {"KOSPI": ["005930"], "KOSDAQ": ["035720"]}

        def fetch_ohlcv(_date: str, market: str) -> pd.DataFrame:
            return _ohlcv(market_tickers[market])

        def fetch_cap(_date: str, market: str) -> pd.DataFrame:
            return _cap(market_tickers[market])

        # When
        backfill([date(2019, 1, 2)], fetch_ohlcv, fetch_cap, paths, markets=MARKETS, sleep=_no_sleep)

        # Then
        saved = pd.read_parquet(paths.snapshots_dir / "2019" / "20190102.parquet")
        assert set(saved["ticker"]) == {"005930", "035720"}
        assert set(saved["market"]) == {"KOSPI", "KOSDAQ"}

    def test_skips_already_collected_date(self, tmp_path: Path):
        """
        목적: 이미 저장된 일자는 조회조차 하지 않는다 (체크포인트, 불변 계약).

        Given: 한 일자가 이미 수집된 상태
        When: 같은 일자를 다시 대상으로 backfill 실행
        Then: 조회 함수가 호출되지 않는다
        """
        # Given
        paths = _paths(tmp_path)
        target = date(2019, 1, 2)
        fetch_ohlcv, fetch_cap = _ok_fetchers()
        backfill([target], fetch_ohlcv, fetch_cap, paths, markets=MARKETS, sleep=_no_sleep)

        call_count = 0

        def counting_fetch(_date: str, _market: str) -> pd.DataFrame:
            nonlocal call_count
            call_count += 1
            return _ohlcv(["005930"])

        # When
        result = backfill([target], counting_fetch, fetch_cap, paths, markets=MARKETS, sleep=_no_sleep)

        # Then
        assert call_count == 0
        assert result.collected == ()


class TestBackfillHoliday:
    """휴장 판정 경로를 고정한다."""

    def test_records_holiday_when_all_markets_empty(self, tmp_path: Path):
        """
        목적: 모든 시장이 빈 결과면 휴장으로 기록한다 (스펙 §7.3).

        Given: 항상 빈 DataFrame을 돌려주는 조회 함수
        When: backfill 실행
        Then: 휴장으로 기록되고 스냅샷 파일은 생기지 않는다
        """
        # Given
        paths = _paths(tmp_path)
        target = date(2019, 1, 1)

        def empty_fetch(_date: str, _market: str) -> pd.DataFrame:
            return pd.DataFrame()

        # When
        result = backfill([target], empty_fetch, empty_fetch, paths, markets=MARKETS, sleep=_no_sleep)

        # Then
        assert result.holidays == (target,)
        assert load_holidays(paths.holidays_path) == {target}
        assert list_collected_dates(base_dir=paths.snapshots_dir) == set()

    def test_records_holiday_when_response_filled_with_zeros(self, tmp_path: Path):
        """
        목적: 값이 0으로 채워진 휴장일 응답을 정상 거래일로 저장하지 않는다.

        pykrx는 휴장일에 빈 결과가 아니라 전 종목 가격 0인 행을 반환한다(2019-01-01 실측).
        빈 결과만 휴장으로 보면 유령 거래일이 불변 parquet에 저장된다.

        Given: 모든 가격·거래량이 0인 조회 결과
        When: backfill 실행
        Then: 휴장으로 기록되고 스냅샷 파일은 생기지 않는다
        """
        # Given
        paths = _paths(tmp_path)
        target = date(2019, 1, 1)

        def zero_ohlcv(_date: str, _market: str) -> pd.DataFrame:
            frame = _ohlcv(["005930"])
            for column in ("시가", "고가", "저가", "종가", "거래량", "거래대금"):
                frame[column] = 0
            return frame

        def zero_cap(_date: str, _market: str) -> pd.DataFrame:
            frame = _cap(["005930"])
            for column in ("종가", "시가총액", "거래량", "거래대금"):
                frame[column] = 0
            return frame

        # When
        result = backfill([target], zero_ohlcv, zero_cap, paths, markets=MARKETS, sleep=_no_sleep)

        # Then
        assert result.holidays == (target,)
        assert result.collected == ()
        assert list_collected_dates(base_dir=paths.snapshots_dir) == set()

    def test_partial_market_result_is_not_holiday(self, tmp_path: Path):
        """
        목적: 일부 시장만 비면 휴장이 아니라 정상 수집으로 처리한다 (경계 조건).

        Given: KOSPI만 결과가 있고 KOSDAQ은 빈 경우
        When: backfill 실행
        Then: 휴장이 아니라 수집 완료로 기록된다
        """
        # Given
        paths = _paths(tmp_path)
        target = date(2019, 1, 2)

        def fetch_ohlcv(_date: str, market: str) -> pd.DataFrame:
            return _ohlcv(["005930"]) if market == "KOSPI" else pd.DataFrame()

        def fetch_cap(_date: str, market: str) -> pd.DataFrame:
            return _cap(["005930"]) if market == "KOSPI" else pd.DataFrame()

        # When
        result = backfill([target], fetch_ohlcv, fetch_cap, paths, markets=MARKETS, sleep=_no_sleep)

        # Then
        assert result.collected == (target,)
        assert load_holidays(paths.holidays_path) == set()


class TestBackfillRetry:
    """재시도·실패 처리 계약을 고정한다."""

    def test_retries_until_success(self, tmp_path: Path):
        """
        목적: 일시적 조회 실패는 재시도로 흡수한다 (스펙 §9).

        Given: 첫 호출만 실패하는 조회 함수
        When: backfill 실행
        Then: 재시도 후 수집에 성공한다
        """
        # Given
        paths = _paths(tmp_path)
        attempts = 0

        def flaky_fetch(_date: str, _market: str) -> pd.DataFrame:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionError("일시적 오류")
            return _ohlcv(["005930"])

        _, fetch_cap = _ok_fetchers()

        # When
        result = backfill([date(2019, 1, 2)], flaky_fetch, fetch_cap, paths, markets=("KOSPI",), sleep=_no_sleep)

        # Then
        assert result.collected == (date(2019, 1, 2),)
        assert attempts == 2

    def test_records_failure_after_attempts_exhausted(self, tmp_path: Path):
        """
        목적: 재시도를 모두 소진하면 실패로 기록하고 다음 일자로 넘어간다.

        Given: 항상 실패하는 조회 함수
        When: backfill 실행
        Then: 실패로 기록되고 휴장으로 오판하지 않는다
        """
        # Given
        paths = _paths(tmp_path)
        target = date(2019, 1, 2)

        def failing_fetch(_date: str, _market: str) -> pd.DataFrame:
            raise ConnectionError("서버 오류")

        # When
        result = backfill(
            [target], failing_fetch, failing_fetch, paths, markets=MARKETS, max_attempts=2, sleep=_no_sleep
        )

        # Then
        assert result.failures == (target,)
        assert target in load_failures(paths.failures_path)
        assert load_holidays(paths.holidays_path) == set()

    def test_continues_to_next_date_after_failure(self, tmp_path: Path):
        """
        목적: 한 일자가 실패해도 나머지 일자를 계속 수집한다.

        Given: 첫 일자만 실패하는 조회 함수
        When: 2일치 backfill 실행
        Then: 두 번째 일자는 정상 수집된다
        """
        # Given
        paths = _paths(tmp_path)
        first, second = date(2019, 1, 2), date(2019, 1, 3)

        def fetch_ohlcv(target_date: str, _market: str) -> pd.DataFrame:
            if target_date == "20190102":
                raise ConnectionError("서버 오류")
            return _ohlcv(["005930"])

        def fetch_cap(_date: str, _market: str) -> pd.DataFrame:
            return _cap(["005930"])

        # When
        result = backfill(
            [first, second], fetch_ohlcv, fetch_cap, paths, markets=("KOSPI",), max_attempts=1, sleep=_no_sleep
        )

        # Then
        assert result.failures == (first,)
        assert result.collected == (second,)

    def test_clears_previous_failure_on_success(self, tmp_path: Path):
        """
        목적: 재수집에 성공하면 이전 실패 기록을 제거한다.

        Given: 실패로 기록된 뒤 정상화된 일자
        When: 다시 backfill 실행
        Then: 실패 목록이 비워진다
        """
        # Given
        paths = _paths(tmp_path)
        target = date(2019, 1, 2)

        def failing_fetch(_date: str, _market: str) -> pd.DataFrame:
            raise ConnectionError("서버 오류")

        backfill([target], failing_fetch, failing_fetch, paths, markets=("KOSPI",), max_attempts=1, sleep=_no_sleep)
        fetch_ohlcv, fetch_cap = _ok_fetchers()

        # When
        backfill([target], fetch_ohlcv, fetch_cap, paths, markets=("KOSPI",), sleep=_no_sleep)

        # Then
        assert load_failures(paths.failures_path) == {}

    def test_rejects_invalid_max_attempts(self, tmp_path: Path):
        """
        목적: 시도 횟수가 1 미만이면 거부한다 (경계 조건).

        Given: max_attempts=0
        When: backfill 호출
        Then: ValueError가 발생한다
        """
        # Given
        paths = _paths(tmp_path)
        fetch_ohlcv, fetch_cap = _ok_fetchers()

        # When / Then
        with pytest.raises(ValueError, match="시도 횟수"):
            backfill(
                [date(2019, 1, 2)], fetch_ohlcv, fetch_cap, paths, markets=MARKETS, max_attempts=0, sleep=_no_sleep
            )
