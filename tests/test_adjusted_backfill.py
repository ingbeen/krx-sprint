"""adjusted_backfill 테스트

2단 수집 루프의 체크포인트·재시도·실패 격리 계약을 고정한다 (스펙 §9).

외부 API는 주입된 스텁으로 대체한다 (tests/CLAUDE.md "외부 의존성 금지").
"""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from krx_sprint.collect.adjusted_backfill import backfill_adjusted
from krx_sprint.collect.adjusted_store import list_collected_tickers, load_adjusted

START = date(2019, 1, 1)
END = date(2019, 1, 3)


def _series() -> pd.DataFrame:
    """pykrx 형태의 수정주가 조회 결과를 만든다."""
    return pd.DataFrame(
        {
            "시가": [1000.0, 1010.0],
            "고가": [1100.0, 1110.0],
            "저가": [900.0, 910.0],
            "종가": [1050.0, 1060.0],
            "거래량": [500, 600],
        },
        index=pd.DatetimeIndex(pd.to_datetime(["2019-01-02", "2019-01-03"]), name="날짜"),
    )


def _noop_sleep(_seconds: float) -> None:
    """실제 대기를 제거한다 (결정적 테스트)."""


class TestBackfillAdjusted:
    """수집 루프 계약을 고정한다."""

    def test_collects_and_saves_each_ticker(self, tmp_path: Path):
        """
        목적: 대상 티커마다 조회 결과를 저장 스키마로 저장한다.

        Given: 항상 정상 결과를 주는 조회 함수
        When: 두 종목을 백필
        Then: 두 파일이 저장되고 결과에 수집 티커가 담긴다
        """

        # Given
        def fetch(_from: str, _to: str, _ticker: str) -> pd.DataFrame:
            return _series()

        # When
        result = backfill_adjusted(
            ["005930", "000660"], fetch, START, END, base_dir=tmp_path, delay_seconds=0, sleep=_noop_sleep
        )

        # Then
        assert result.collected == ("005930", "000660")
        assert result.failures == ()
        assert list_collected_tickers(base_dir=tmp_path) == {"005930", "000660"}

    def test_passes_date_range_in_krx_format(self, tmp_path: Path):
        """
        목적: 조회 구간을 pykrx 형식(YYYYMMDD)으로 전달한다.

        Given: 인자를 기록하는 조회 함수
        When: 한 종목을 백필
        Then: 시작일·종료일·티커가 규칙대로 전달된다
        """
        # Given
        calls: list[tuple[str, str, str]] = []

        def fetch(from_date: str, to_date: str, ticker: str) -> pd.DataFrame:
            calls.append((from_date, to_date, ticker))
            return _series()

        # When
        backfill_adjusted(["005930"], fetch, START, END, base_dir=tmp_path, delay_seconds=0, sleep=_noop_sleep)

        # Then
        assert calls == [("20190101", "20190103", "005930")]

    def test_collects_alphanumeric_ticker(self, tmp_path: Path):
        """
        목적: 영문이 섞인 티커를 수집 실패로 떨어뜨리지 않는다 (회귀 방지).

        시범 수집에서 `0001A0`이 "티커는 6자리 숫자" 검증에 걸려 실패했다.
        KRX 티커는 숫자 전용이 아니며 실측 유니버스 3,135종목 중 78종목이 영문을 포함한다.

        Given: 신형 종목코드 형태의 티커
        When: 백필
        Then: 실패 없이 저장된다
        """

        # Given
        def fetch(_from: str, _to: str, _ticker: str) -> pd.DataFrame:
            return _series()

        # When
        result = backfill_adjusted(["0001A0"], fetch, START, END, base_dir=tmp_path, delay_seconds=0, sleep=_noop_sleep)

        # Then
        assert result.collected == ("0001A0",)
        assert result.failures == ()

    def test_skips_already_collected(self, tmp_path: Path):
        """
        목적: 이미 저장된 종목은 조회하지 않는다 (파일 존재 = 체크포인트).

        Given: 한 종목이 이미 저장된 상태
        When: 같은 종목을 다시 백필
        Then: 조회가 일어나지 않고 수집 결과가 비어 있다
        """

        # Given
        def fetch(_from: str, _to: str, _ticker: str) -> pd.DataFrame:
            return _series()

        backfill_adjusted(["005930"], fetch, START, END, base_dir=tmp_path, delay_seconds=0, sleep=_noop_sleep)

        calls: list[str] = []

        def counting_fetch(_from: str, _to: str, ticker: str) -> pd.DataFrame:
            calls.append(ticker)
            return _series()

        # When
        result = backfill_adjusted(
            ["005930"], counting_fetch, START, END, base_dir=tmp_path, delay_seconds=0, sleep=_noop_sleep
        )

        # Then
        assert calls == []
        assert result.collected == ()

    def test_isolates_failure_per_ticker(self, tmp_path: Path):
        """
        목적: 한 종목의 실패가 나머지 종목 수집을 막지 않는다 (스펙 §9).

        Given: 특정 티커에서만 예외를 던지는 조회 함수
        When: 두 종목을 백필
        Then: 실패한 종목만 실패로 남고 나머지는 저장된다
        """

        # Given
        def fetch(_from: str, _to: str, ticker: str) -> pd.DataFrame:
            if ticker == "000660":
                raise RuntimeError("조회 실패")
            return _series()

        # When
        result = backfill_adjusted(
            ["005930", "000660"],
            fetch,
            START,
            END,
            base_dir=tmp_path,
            max_attempts=1,
            delay_seconds=0,
            sleep=_noop_sleep,
        )

        # Then
        assert result.collected == ("005930",)
        assert result.failures == ("000660",)

    def test_retries_before_failing(self, tmp_path: Path):
        """
        목적: 일시적 조회 실패를 재시도로 흡수한다 (지수 백오프).

        Given: 첫 두 번은 실패하고 세 번째에 성공하는 조회 함수
        When: 최대 3회 시도로 백필
        Then: 수집에 성공한다
        """
        # Given
        attempts: list[int] = []

        def fetch(_from: str, _to: str, _ticker: str) -> pd.DataFrame:
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("일시적 실패")
            return _series()

        # When
        result = backfill_adjusted(
            ["005930"], fetch, START, END, base_dir=tmp_path, max_attempts=3, delay_seconds=0, sleep=_noop_sleep
        )

        # Then
        assert result.collected == ("005930",)
        assert len(attempts) == 3

    def test_records_failure_after_exhausting_attempts(self, tmp_path: Path):
        """
        목적: 재시도를 모두 소진하면 실패로 남기고 파일을 만들지 않는다.

        Given: 항상 실패하는 조회 함수
        When: 최대 2회 시도로 백필
        Then: 실패로 기록되고 저장 파일이 없다
        """

        # Given
        def fetch(_from: str, _to: str, _ticker: str) -> pd.DataFrame:
            raise RuntimeError("영구 실패")

        # When
        result = backfill_adjusted(
            ["005930"], fetch, START, END, base_dir=tmp_path, max_attempts=2, delay_seconds=0, sleep=_noop_sleep
        )

        # Then
        assert result.failures == ("005930",)
        assert list_collected_tickers(base_dir=tmp_path) == set()

    def test_treats_empty_result_as_failure(self, tmp_path: Path):
        """
        목적: 빈 조회 결과를 빈 파일로 저장하지 않는다 (체크포인트 오염 방지, 경계 조건).

        Given: 빈 DataFrame을 주는 조회 함수
        When: 백필
        Then: 실패로 기록되고 파일이 생기지 않는다
        """

        # Given
        def fetch(_from: str, _to: str, _ticker: str) -> pd.DataFrame:
            return _series().iloc[0:0]

        # When
        result = backfill_adjusted(
            ["005930"], fetch, START, END, base_dir=tmp_path, max_attempts=1, delay_seconds=0, sleep=_noop_sleep
        )

        # Then
        assert result.failures == ("005930",)
        assert list_collected_tickers(base_dir=tmp_path) == set()

    def test_saves_storage_schema(self, tmp_path: Path):
        """
        목적: 저장 결과가 2단 스키마와 dtype 정책을 따른다.

        Given: 정상 조회 결과
        When: 백필 후 로드
        Then: 일자 오름차순의 저장 스키마가 된다
        """

        # Given
        def fetch(_from: str, _to: str, _ticker: str) -> pd.DataFrame:
            return _series()

        # When
        backfill_adjusted(["005930"], fetch, START, END, base_dir=tmp_path, delay_seconds=0, sleep=_noop_sleep)

        # Then
        loaded = load_adjusted("005930", base_dir=tmp_path)
        assert loaded["date"].tolist() == [pd.Timestamp("2019-01-02"), pd.Timestamp("2019-01-03")]
        assert loaded["close"].tolist() == pytest.approx([1050.0, 1060.0])

    def test_rejects_invalid_max_attempts(self, tmp_path: Path):
        """
        목적: 시도 횟수 파라미터를 즉시 검증한다 (경계 조건).

        Given: 0회 시도
        When: backfill_adjusted 호출
        Then: ValueError가 발생한다
        """
        # Given / When / Then
        with pytest.raises(ValueError, match="시도 횟수"):
            backfill_adjusted(["005930"], lambda *_: _series(), START, END, base_dir=tmp_path, max_attempts=0)

    def test_rejects_reversed_range(self, tmp_path: Path):
        """
        목적: 시작일이 종료일보다 늦으면 즉시 예외를 낸다 (경계 조건).

        Given: 뒤집힌 조회 구간
        When: backfill_adjusted 호출
        Then: ValueError가 발생한다
        """
        # Given / When / Then
        with pytest.raises(ValueError, match="종료일"):
            backfill_adjusted(["005930"], lambda *_: _series(), END, START, base_dir=tmp_path)
