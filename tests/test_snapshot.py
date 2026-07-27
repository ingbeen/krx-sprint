"""snapshot 테스트

1단 전종목 스냅샷의 저장 스키마와 저장 전 검증 정책을 고정한다 (스펙 §7.2·§8).
"""

import pandas as pd
import pytest

from krx_sprint.collect.snapshot import build_snapshot, is_market_closed, validate_snapshot
from krx_sprint.common_constants import (
    COL_CHANGE_RATE,
    COL_CLOSE,
    COL_HIGH,
    COL_LOW,
    COL_MARKET,
    COL_MARKET_CAP,
    COL_OPEN,
    COL_SHARES,
    COL_TICKER,
    COL_VALUE,
    COL_VOLUME,
    SNAPSHOT_COLUMNS,
)

MARKET = "KOSPI"


def _ohlcv(tickers: list[str], **overrides: list[int] | list[float]) -> pd.DataFrame:
    """pykrx get_market_ohlcv_by_ticker 형태의 최소 DataFrame을 만든다."""
    count = len(tickers)
    data: dict[str, list[int] | list[float]] = {
        "시가": [1000] * count,
        "고가": [1100] * count,
        "저가": [900] * count,
        "종가": [1050] * count,
        "거래량": [500] * count,
        "거래대금": [525000] * count,
        "등락률": [1.5] * count,
    }
    data.update(overrides)
    return pd.DataFrame(data, index=pd.Index(tickers, name="티커"))


def _cap(tickers: list[str], **overrides: list[int]) -> pd.DataFrame:
    """pykrx get_market_cap_by_ticker 형태의 최소 DataFrame을 만든다."""
    count = len(tickers)
    data: dict[str, list[int]] = {
        "종가": [1050] * count,
        "시가총액": [10500000] * count,
        "거래량": [500] * count,
        "거래대금": [525000] * count,
        "상장주식수": [10000] * count,
    }
    data.update(overrides)
    return pd.DataFrame(data, index=pd.Index(tickers, name="티커"))


class TestIsMarketClosed:
    """휴장일 응답 판정 계약을 고정한다.

    pykrx는 휴장일에 빈 결과가 아니라 값이 0으로 채워진 행을 반환한다(실측).
    빈 결과만 휴장으로 보면 휴장일이 정상 거래일로 저장된다.
    """

    def test_detects_holiday_response_filled_with_zeros(self):
        """
        목적: 모든 가격이 0인 응답을 휴장으로 판정한다 (2019-01-01 신정 실측 패턴).

        Given: 시가·고가·저가·종가가 모두 0인 조회 결과
        When: is_market_closed 호출
        Then: True를 반환한다
        """
        # Given
        tickers = ["005930", "000660"]
        closed = _ohlcv(tickers, 시가=[0, 0], 고가=[0, 0], 저가=[0, 0], 종가=[0, 0], 거래량=[0, 0])

        # When / Then
        assert is_market_closed(closed) is True

    def test_detects_empty_response_as_closed(self):
        """
        목적: 빈 결과도 휴장으로 판정한다 (경계 조건).

        Given: 빈 DataFrame
        When: is_market_closed 호출
        Then: True를 반환한다
        """
        # Given / When / Then
        assert is_market_closed(pd.DataFrame()) is True

    def test_normal_trading_day_is_not_closed(self):
        """
        목적: 정상 거래일을 휴장으로 오판하지 않는다.

        Given: 정상 조회 결과
        When: is_market_closed 호출
        Then: False를 반환한다
        """
        # Given / When / Then
        assert is_market_closed(_ohlcv(["005930"])) is False

    def test_individual_halted_stock_is_not_closed(self):
        """
        목적: 개별 거래정지 종목이 섞여 있어도 휴장으로 보지 않는다 (경계 조건).

        Given: 한 종목만 거래정지(가격 0)이고 나머지는 정상인 결과
        When: is_market_closed 호출
        Then: False를 반환한다
        """
        # Given
        tickers = ["005930", "000660"]
        mixed = _ohlcv(
            tickers,
            시가=[0, 1000],
            고가=[0, 1100],
            저가=[0, 900],
            종가=[0, 1050],
            거래량=[0, 500],
        )

        # When / Then
        assert is_market_closed(mixed) is False


class TestBuildSnapshot:
    """조회 결과 → 저장 스키마 변환 계약을 고정한다."""

    def test_produces_spec_schema(self):
        """
        목적: 저장 스키마의 컬럼 구성과 순서를 고정한다 (스펙 §7.2).

        Given: OHLCV와 시가총액 조회 결과
        When: build_snapshot 호출
        Then: SNAPSHOT_COLUMNS 순서 그대로의 컬럼을 가진다
        """
        # Given
        tickers = ["005930", "000660"]

        # When
        result = build_snapshot(_ohlcv(tickers), _cap(tickers), MARKET)

        # Then
        assert list(result.columns) == SNAPSHOT_COLUMNS

    def test_preserves_ticker_leading_zero_as_string(self):
        """
        목적: 티커의 선행 0이 보존되고 문자열로 저장된다 (int 금지).

        Given: 선행 0으로 시작하는 티커
        When: build_snapshot 호출
        Then: ticker 컬럼이 6자리 문자열 그대로다
        """
        # Given
        tickers = ["005930"]

        # When
        result = build_snapshot(_ohlcv(tickers), _cap(tickers), MARKET)

        # Then
        assert result[COL_TICKER].tolist() == ["005930"]
        assert result[COL_TICKER].dtype == object

    def test_assigns_market_label(self):
        """
        목적: 시장 구분 라벨을 컬럼으로 부여한다 (pykrx 반환값에 없는 필드).

        Given: KOSDAQ 조회 결과
        When: build_snapshot 호출
        Then: market 컬럼이 모두 KOSDAQ이다
        """
        # Given
        tickers = ["035720", "247540"]

        # When
        result = build_snapshot(_ohlcv(tickers), _cap(tickers), "KOSDAQ")

        # Then
        assert result[COL_MARKET].tolist() == ["KOSDAQ", "KOSDAQ"]

    def test_casts_price_columns_to_int64(self):
        """
        목적: 원화 가격·거래량·시총·상장주식수는 int64로 저장한다.

        Given: 정상 조회 결과
        When: build_snapshot 호출
        Then: 등락률을 제외한 수치 컬럼이 int64다
        """
        # Given
        tickers = ["005930"]

        # When
        result = build_snapshot(_ohlcv(tickers), _cap(tickers), MARKET)

        # Then
        int_columns = [
            COL_OPEN,
            COL_HIGH,
            COL_LOW,
            COL_CLOSE,
            COL_VOLUME,
            COL_VALUE,
            COL_MARKET_CAP,
            COL_SHARES,
        ]
        for column in int_columns:
            assert result[column].dtype == "int64", f"{column} dtype 불일치"

    def test_keeps_change_rate_as_float(self):
        """
        목적: 등락률은 pykrx 반환값(% 단위)을 float64로 유지한다.

        Given: 등락률 1.5인 조회 결과
        When: build_snapshot 호출
        Then: change_rate가 float64이고 값이 유지된다
        """
        # Given
        tickers = ["005930"]

        # When
        result = build_snapshot(_ohlcv(tickers), _cap(tickers), MARKET)

        # Then
        assert result[COL_CHANGE_RATE].dtype == "float64"
        assert result[COL_CHANGE_RATE].iloc[0] == pytest.approx(1.5)

    def test_does_not_mutate_input_frames(self):
        """
        목적: 원본 DataFrame을 변경하지 않는다 (데이터 불변성).

        Given: 조회 결과 DataFrame 2개
        When: build_snapshot 호출
        Then: 원본의 컬럼 구성이 그대로다
        """
        # Given
        tickers = ["005930"]
        ohlcv = _ohlcv(tickers)
        cap = _cap(tickers)
        ohlcv_columns = list(ohlcv.columns)
        cap_columns = list(cap.columns)

        # When
        build_snapshot(ohlcv, cap, MARKET)

        # Then
        assert list(ohlcv.columns) == ohlcv_columns
        assert list(cap.columns) == cap_columns

    def test_rejects_ticker_set_mismatch(self):
        """
        목적: 두 조회 결과의 티커 집합이 다르면 조용히 교집합을 쓰지 않는다.

        Given: 시가총액 결과에만 있는 티커
        When: build_snapshot 호출
        Then: ValueError가 발생한다
        """
        # Given
        ohlcv = _ohlcv(["005930"])
        cap = _cap(["005930", "000660"])

        # When / Then
        with pytest.raises(ValueError, match="티커 집합"):
            build_snapshot(ohlcv, cap, MARKET)

    def test_rejects_close_price_mismatch(self):
        """
        목적: 두 조회 결과의 종가가 다르면 조회 시점 어긋남으로 보고 중단한다.

        Given: 종가가 서로 다른 두 결과
        When: build_snapshot 호출
        Then: ValueError가 발생한다
        """
        # Given
        tickers = ["005930"]
        ohlcv = _ohlcv(tickers)
        cap = _cap(tickers, 종가=[9999])

        # When / Then
        with pytest.raises(ValueError, match="종가"):
            build_snapshot(ohlcv, cap, MARKET)

    def test_rejects_empty_input(self):
        """
        목적: 빈 조회 결과를 스냅샷으로 만들지 않는다 (휴장은 호출자가 판정, 경계 조건).

        Given: 빈 OHLCV 결과
        When: build_snapshot 호출
        Then: ValueError가 발생한다
        """
        # Given / When / Then
        with pytest.raises(ValueError, match="비어"):
            build_snapshot(pd.DataFrame(), _cap(["005930"]), MARKET)

    def test_rejects_unknown_market(self):
        """
        목적: 수집 대상이 아닌 시장 라벨을 거부한다 (코넥스 혼입 차단).

        Given: KONEX 시장 라벨
        When: build_snapshot 호출
        Then: ValueError가 발생한다
        """
        # Given
        tickers = ["900110"]

        # When / Then
        with pytest.raises(ValueError, match="시장"):
            build_snapshot(_ohlcv(tickers), _cap(tickers), "KONEX")

    def test_rejects_missing_source_column(self):
        """
        목적: pykrx 반환 컬럼이 바뀌면 즉시 인지한다.

        Given: 등락률 컬럼이 빠진 조회 결과
        When: build_snapshot 호출
        Then: ValueError가 발생한다
        """
        # Given
        tickers = ["005930"]
        ohlcv = _ohlcv(tickers).drop(columns=["등락률"])

        # When / Then
        with pytest.raises(ValueError, match="컬럼"):
            build_snapshot(ohlcv, _cap(tickers), MARKET)

    def test_rejects_nan_values(self):
        """
        목적: 결측치를 보간하거나 0으로 채우지 않는다 (경계 조건).

        Given: 상장주식수가 NaN인 결과
        When: build_snapshot 호출
        Then: ValueError가 발생한다
        """
        # Given
        tickers = ["005930"]
        cap = _cap(tickers)
        cap["상장주식수"] = [float("nan")]

        # When / Then
        with pytest.raises(ValueError, match="결측"):
            build_snapshot(_ohlcv(tickers), cap, MARKET)


class TestValidateSnapshot:
    """저장 전 무결성 검증 정책을 고정한다 (스펙 §8)."""

    def _snapshot(self, **overrides: list[int] | list[float] | list[str]) -> pd.DataFrame:
        """검증용 최소 스냅샷을 만든다."""
        data: dict[str, list[int] | list[float] | list[str]] = {
            COL_TICKER: ["005930"],
            COL_MARKET: [MARKET],
            COL_OPEN: [1000],
            COL_HIGH: [1100],
            COL_LOW: [900],
            COL_CLOSE: [1050],
            COL_VOLUME: [500],
            COL_VALUE: [525000],
            COL_CHANGE_RATE: [1.5],
            COL_MARKET_CAP: [10500000],
            COL_SHARES: [10000],
        }
        data.update(overrides)
        return pd.DataFrame(data)[SNAPSHOT_COLUMNS]

    def test_accepts_normal_snapshot(self):
        """
        목적: 정상 스냅샷은 경고 없이 통과한다.

        Given: 정상 값만 가진 스냅샷
        When: validate_snapshot 호출
        Then: 경고가 없다
        """
        # Given / When
        warnings = validate_snapshot(self._snapshot())

        # Then
        assert warnings == ()

    def test_accepts_halted_pattern_as_normal(self):
        """
        목적: 거래량 0 + OHL 0은 거래정지로 보고 통과시킨다 (스펙 §8, Phase 1 실측 근거).

        Given: 거래량 0이고 시가·고가·저가가 0인 종목
        When: validate_snapshot 호출
        Then: 예외 없이 통과한다
        """
        # Given
        snapshot = self._snapshot(**{COL_VOLUME: [0], COL_OPEN: [0], COL_HIGH: [0], COL_LOW: [0], COL_VALUE: [0]})

        # When
        warnings = validate_snapshot(snapshot)

        # Then
        assert warnings == ()

    def test_rejects_zero_close_with_volume(self):
        """
        목적: 거래량이 있는데 종가가 없으면 이상치로 보고 중단한다 (스펙 §8).

        종가는 거래가 있었다면 반드시 존재해야 하는 유일한 가격이다.

        Given: 거래량 500인데 종가 0인 종목
        When: validate_snapshot 호출
        Then: ValueError가 발생한다
        """
        # Given
        snapshot = self._snapshot(**{COL_CLOSE: [0]})

        # When / Then
        with pytest.raises(ValueError, match="종가"):
            validate_snapshot(snapshot)

    def test_accepts_zero_ohl_when_close_exists(self):
        """
        목적: 시가·고가·저가만 0이고 종가·거래량이 정상인 행을 통과시킨다.

        정규장에서 가격이 형성되지 않고 시간외 거래 등으로만 체결된 날의 실측 패턴이다
        (2019-02-11 056730, 2019-05-29 310200·270520). 이를 이상치로 막으면 해당 일자가
        통째로 수집되지 못해 데이터에 구멍이 생긴다.

        Given: 시가·고가·저가 0, 종가 1460, 거래량 60795
        When: validate_snapshot 호출
        Then: 예외 없이 통과한다
        """
        # Given
        snapshot = self._snapshot(
            **{
                COL_OPEN: [0],
                COL_HIGH: [0],
                COL_LOW: [0],
                COL_CLOSE: [1460],
                COL_VOLUME: [60795],
                COL_VALUE: [88760700],
                COL_CHANGE_RATE: [0.0],
            }
        )

        # When
        warnings = validate_snapshot(snapshot)

        # Then
        assert warnings == ()

    def test_rejects_negative_price(self):
        """
        목적: 음수 가격을 거부한다.

        Given: 저가가 음수인 종목
        When: validate_snapshot 호출
        Then: ValueError가 발생한다
        """
        # Given
        snapshot = self._snapshot(**{COL_LOW: [-100]})

        # When / Then
        with pytest.raises(ValueError, match="가격"):
            validate_snapshot(snapshot)

    def test_rejects_high_lower_than_low(self):
        """
        목적: 고가 < 저가인 데이터를 거부한다.

        Given: 고가가 저가보다 낮은 종목
        When: validate_snapshot 호출
        Then: ValueError가 발생한다
        """
        # Given
        snapshot = self._snapshot(**{COL_HIGH: [800]})

        # When / Then
        with pytest.raises(ValueError, match="고가"):
            validate_snapshot(snapshot)

    def test_warns_on_change_rate_beyond_price_limit(self):
        """
        목적: 가격제한폭을 넘는 등락률은 중단하지 않고 경고로 남긴다 (권리락 등 특이일).

        Given: 등락률 -45%인 종목
        When: validate_snapshot 호출
        Then: 경고가 1건 반환되고 예외는 발생하지 않는다
        """
        # Given
        snapshot = self._snapshot(**{COL_CHANGE_RATE: [-45.0]})

        # When
        warnings = validate_snapshot(snapshot)

        # Then
        assert len(warnings) == 1
        assert "005930" in warnings[0]
