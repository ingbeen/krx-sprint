"""adjusted_store 테스트

2단 종목별 parquet의 저장 계약을 고정한다 (스펙 §7.2).

1단 스냅샷과 달리 **가변 파일**이다 — 분할·증자가 발생하면 과거 전체가 재계산되므로
같은 티커 파일을 다시 쓸 수 있어야 한다. 이 차이를 테스트로 못박는다.
"""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from krx_sprint.collect.adjusted_store import (
    adjusted_path,
    list_collected_tickers,
    load_adjusted,
    save_adjusted,
)
from krx_sprint.common_constants import ADJUSTED_COLUMNS

TICKER = "005930"


def _adjusted(closes: list[float] | None = None) -> pd.DataFrame:
    """저장용 최소 시계열을 만든다."""
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2019-01-02", "2019-01-03"]),
            "open": [1000.0, 1010.0],
            "high": [1100.0, 1110.0],
            "low": [900.0, 910.0],
            "close": closes if closes is not None else [1050.0, 1060.0],
            "volume": [500, 600],
        }
    )
    return frame[ADJUSTED_COLUMNS]


class TestAdjustedPath:
    """저장 경로 규칙을 고정한다."""

    def test_uses_ticker_filename(self):
        """
        목적: 경로가 {ticker}.parquet 규칙을 따르고 선행 0을 보존한다.

        Given: 선행 0이 있는 티커
        When: adjusted_path 호출
        Then: 티커 그대로의 파일명이 된다
        """
        # Given / When
        result = adjusted_path("005930", base_dir=Path("/tmp/adjusted"))

        # Then
        assert result == Path("/tmp/adjusted/005930.parquet")

    def test_rejects_invalid_ticker(self):
        """
        목적: 티커 형식을 경로 생성 시점에 검증한다 (경계 조건).

        Given: 6자리가 아닌 티커
        When: adjusted_path 호출
        Then: ValueError가 발생한다
        """
        # Given / When / Then
        with pytest.raises(ValueError, match="티커"):
            adjusted_path("5930", base_dir=Path("/tmp/adjusted"))

    def test_accepts_alphanumeric_ticker(self):
        """
        목적: 영문이 섞인 티커도 경로를 만든다 (회귀 방지).

        Given: 신형 종목코드 형태의 티커
        When: adjusted_path 호출
        Then: 티커 그대로의 파일명이 된다
        """
        # Given / When
        result = adjusted_path("0001A0", base_dir=Path("/tmp/adjusted"))

        # Then
        assert result == Path("/tmp/adjusted/0001A0.parquet")


class TestSaveAdjusted:
    """저장 계약(가변 파일·스키마·반올림)을 고정한다."""

    def test_creates_parquet(self, tmp_path: Path):
        """
        목적: 저장 폴더를 만들고 parquet을 기록한다.

        Given: 비어 있는 저장 경로
        When: save_adjusted 호출
        Then: 파일이 생성되고 스키마가 유지된다
        """
        # Given / When
        saved = save_adjusted(_adjusted(), TICKER, base_dir=tmp_path)

        # Then
        assert saved.exists()
        assert list(pd.read_parquet(saved).columns) == ADJUSTED_COLUMNS

    def test_allows_overwrite(self, tmp_path: Path):
        """
        목적: 같은 티커 파일을 다시 쓸 수 있다 (1단의 불변 계약과 다른 지점).

        Given: 이미 저장된 티커
        When: 다른 값으로 다시 save_adjusted 호출
        Then: 예외 없이 새 값으로 대체된다
        """
        # Given
        save_adjusted(_adjusted(), TICKER, base_dir=tmp_path)

        # When
        save_adjusted(_adjusted(closes=[2050.0, 2060.0]), TICKER, base_dir=tmp_path)

        # Then
        loaded = load_adjusted(TICKER, base_dir=tmp_path)
        assert loaded["close"].tolist() == pytest.approx([2050.0, 2060.0])

    def test_rounds_prices_on_save(self, tmp_path: Path):
        """
        목적: 저장 직전 가격을 6자리로 반올림한다 (루트 CLAUDE.md 반올림 규칙).

        Given: 소수점 7자리 이상인 종가
        When: save_adjusted 후 로드
        Then: 6자리로 반올림된 값이 저장된다
        """
        # Given
        frame = _adjusted(closes=[100.1234567, 200.7654321])

        # When
        save_adjusted(frame, TICKER, base_dir=tmp_path)

        # Then
        loaded = load_adjusted(TICKER, base_dir=tmp_path)
        assert loaded["close"].tolist() == pytest.approx([100.123457, 200.765432], abs=1e-9)

    def test_rejects_wrong_schema(self, tmp_path: Path):
        """
        목적: 스키마가 어긋난 DataFrame을 저장하지 않는다.

        Given: 컬럼이 빠진 시계열
        When: save_adjusted 호출
        Then: ValueError가 발생한다
        """
        # Given
        broken = _adjusted().drop(columns=["volume"])

        # When / Then
        with pytest.raises(ValueError, match="컬럼"):
            save_adjusted(broken, TICKER, base_dir=tmp_path)

    def test_rejects_empty_frame(self, tmp_path: Path):
        """
        목적: 빈 시계열을 파일로 남기지 않는다 (체크포인트 오염 방지).

        Given: 행이 없는 시계열
        When: save_adjusted 호출
        Then: ValueError가 발생한다
        """
        # Given
        empty = _adjusted().iloc[0:0]

        # When / Then
        with pytest.raises(ValueError, match="비어"):
            save_adjusted(empty, TICKER, base_dir=tmp_path)

    def test_does_not_mutate_input(self, tmp_path: Path):
        """
        목적: 반올림이 원본 DataFrame을 변경하지 않는다 (데이터 불변성).

        Given: 소수점이 긴 종가
        When: save_adjusted 호출
        Then: 입력 값이 그대로 남는다
        """
        # Given
        frame = _adjusted(closes=[100.1234567, 200.7654321])

        # When
        save_adjusted(frame, TICKER, base_dir=tmp_path)

        # Then
        assert frame["close"].tolist() == pytest.approx([100.1234567, 200.7654321])


class TestLoadAdjusted:
    """로드 계약을 고정한다."""

    def test_loads_saved_series(self, tmp_path: Path):
        """
        목적: 저장한 시계열을 스키마 그대로 다시 읽는다.

        Given: 저장된 시계열
        When: load_adjusted 호출
        Then: 컬럼과 행 수가 유지된다
        """
        # Given
        save_adjusted(_adjusted(), TICKER, base_dir=tmp_path)

        # When
        loaded = load_adjusted(TICKER, base_dir=tmp_path)

        # Then
        assert list(loaded.columns) == ADJUSTED_COLUMNS
        assert len(loaded) == 2

    def test_rejects_missing_file(self, tmp_path: Path):
        """
        목적: 없는 티커를 조용히 빈 결과로 넘기지 않는다 (경계 조건).

        Given: 저장되지 않은 티커
        When: load_adjusted 호출
        Then: FileNotFoundError가 발생한다
        """
        # Given / When / Then
        with pytest.raises(FileNotFoundError, match="005930"):
            load_adjusted(TICKER, base_dir=tmp_path)

    def test_truncates_before_since(self, tmp_path: Path):
        """
        목적: 유니버스 밖 구간(1단 최초 등장일 이전)을 로드 시점에 절단한다.

        2단은 1단이 제외한 시장의 이전상장 이력까지 담고 있고 그 구간은 가격 축이 다르다.
        저장 데이터는 원본 그대로 두고 소비 시점에 거른다.

        Given: 2일치가 저장된 시계열
        When: 둘째 날을 기준으로 load_adjusted 호출
        Then: 첫날 행이 제거되고 인덱스가 초기화된다
        """
        # Given
        save_adjusted(_adjusted(), TICKER, base_dir=tmp_path)

        # When
        loaded = load_adjusted(TICKER, base_dir=tmp_path, since=date(2019, 1, 3))

        # Then
        assert loaded["date"].tolist() == [pd.Timestamp("2019-01-03")]
        assert loaded.index.tolist() == [0]
        assert list(loaded.columns) == ADJUSTED_COLUMNS

    def test_keeps_all_rows_without_since(self, tmp_path: Path):
        """
        목적: 절단 기준을 주지 않으면 기존 동작을 그대로 유지한다 (기본값 계약).

        Given: 2일치가 저장된 시계열
        When: since 없이 load_adjusted 호출
        Then: 모든 행이 반환된다
        """
        # Given
        save_adjusted(_adjusted(), TICKER, base_dir=tmp_path)

        # When
        loaded = load_adjusted(TICKER, base_dir=tmp_path)

        # Then
        assert len(loaded) == 2

    def test_returns_empty_when_since_after_range(self, tmp_path: Path):
        """
        목적: 기준 일자가 시계열 이후면 빈 결과를 반환한다 (필터이므로 예외가 아니다, 경계 조건).

        Given: 2019년 시계열
        When: 2020년을 기준으로 load_adjusted 호출
        Then: 행이 없고 스키마는 유지된다
        """
        # Given
        save_adjusted(_adjusted(), TICKER, base_dir=tmp_path)

        # When
        loaded = load_adjusted(TICKER, base_dir=tmp_path, since=date(2020, 1, 1))

        # Then
        assert loaded.empty
        assert list(loaded.columns) == ADJUSTED_COLUMNS


class TestListCollectedTickers:
    """수집 완료 체크포인트(파일 존재) 판정을 고정한다."""

    def test_returns_saved_tickers(self, tmp_path: Path):
        """
        목적: 저장된 파일명에서 수집 완료 티커를 복원한다.

        Given: 두 티커가 저장된 상태
        When: list_collected_tickers 호출
        Then: 두 티커가 모두 반환된다
        """
        # Given
        save_adjusted(_adjusted(), "005930", base_dir=tmp_path)
        save_adjusted(_adjusted(), "000660", base_dir=tmp_path)

        # When
        result = list_collected_tickers(base_dir=tmp_path)

        # Then
        assert result == {"005930", "000660"}

    def test_includes_alphanumeric_ticker(self, tmp_path: Path):
        """
        목적: 영문이 섞인 티커 파일도 체크포인트로 인식한다 (회귀 방지).

        Given: 신형 종목코드로 저장된 파일
        When: list_collected_tickers 호출
        Then: 해당 티커가 반환된다
        """
        # Given
        save_adjusted(_adjusted(), "0001A0", base_dir=tmp_path)

        # When
        result = list_collected_tickers(base_dir=tmp_path)

        # Then
        assert result == {"0001A0"}

    def test_returns_empty_for_missing_directory(self, tmp_path: Path):
        """
        목적: 저장 폴더가 없으면 빈 집합을 반환한다 (최초 실행, 경계 조건).

        Given: 존재하지 않는 폴더
        When: list_collected_tickers 호출
        Then: 빈 집합이다
        """
        # Given / When
        result = list_collected_tickers(base_dir=tmp_path / "없음")

        # Then
        assert result == set()

    def test_rejects_malformed_filename(self, tmp_path: Path):
        """
        목적: 티커 규칙에 맞지 않는 파일을 조용히 건너뛰지 않는다.

        Given: 티커 형식이 아닌 parquet 파일
        When: list_collected_tickers 호출
        Then: ValueError가 발생한다
        """
        # Given
        (tmp_path / "invalid.parquet").touch()

        # When / Then
        with pytest.raises(ValueError, match="파일명"):
            list_collected_tickers(base_dir=tmp_path)
