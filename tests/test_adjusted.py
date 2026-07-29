"""adjusted 변환·검증 테스트

2단 수정주가 시계열의 저장 스키마와 이상치 판정 정책을 고정한다 (스펙 §7.2·§8).

1단과 같은 정책을 공유한다 — 반드시 있어야 하는 값은 **종가**이며,
시가·고가·저가가 0인 것은 거래정지·정규장 미형성의 정상 패턴이다.
"""

import pandas as pd
import pytest

from krx_sprint.collect.adjusted import build_adjusted, validate_adjusted
from krx_sprint.common_constants import ADJUSTED_COLUMNS

TICKER = "005930"


def _series(
    dates: list[str] | None = None,
    opens: list[float] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    closes: list[float] | None = None,
    volumes: list[int] | None = None,
) -> pd.DataFrame:
    """pykrx `get_market_ohlcv` 반환 형태(한글 컬럼 + 날짜 인덱스)를 만든다."""
    dates = dates if dates is not None else ["2019-01-02", "2019-01-03"]
    return pd.DataFrame(
        {
            "시가": opens if opens is not None else [1000.0, 1010.0],
            "고가": highs if highs is not None else [1100.0, 1110.0],
            "저가": lows if lows is not None else [900.0, 910.0],
            "종가": closes if closes is not None else [1050.0, 1060.0],
            "거래량": volumes if volumes is not None else [500, 600],
        },
        index=pd.DatetimeIndex(pd.to_datetime(dates), name="날짜"),
    )


def _adjusted() -> pd.DataFrame:
    """저장 스키마의 최소 시계열을 만든다."""
    return build_adjusted(_series(), TICKER)


class TestBuildAdjusted:
    """조회 결과 → 저장 스키마 변환 계약을 고정한다."""

    def test_converts_to_storage_schema(self):
        """
        목적: 한글 컬럼과 날짜 인덱스를 저장 스키마로 변환한다.

        Given: pykrx 형태의 시계열
        When: build_adjusted 호출
        Then: 컬럼 순서가 ADJUSTED_COLUMNS와 같고 인덱스가 초기화된다
        """
        # Given / When
        result = build_adjusted(_series(), TICKER)

        # Then
        assert list(result.columns) == ADJUSTED_COLUMNS
        assert result.index.tolist() == [0, 1]

    def test_fixes_dtypes(self):
        """
        목적: 가격은 float64, 거래량은 int64, 일자는 datetime64로 고정한다.

        Given: pykrx 형태의 시계열
        When: build_adjusted 호출
        Then: dtype이 저장 정책과 일치한다
        """
        # Given / When
        result = build_adjusted(_series(), TICKER)

        # Then
        assert result["date"].dtype == "datetime64[ns]"
        assert all(result[column].dtype == "float64" for column in ("open", "high", "low", "close"))
        assert result["volume"].dtype == "int64"

    def test_keeps_fractional_price(self):
        """
        목적: 수정주가의 소수 값을 정수로 잘라내지 않는다.

        Given: 종가에 소수가 있는 시계열
        When: build_adjusted 호출
        Then: 소수 값이 보존된다
        """
        # Given
        series = _series(closes=[1050.5, 1060.25])

        # When
        result = build_adjusted(series, TICKER)

        # Then
        assert result["close"].tolist() == pytest.approx([1050.5, 1060.25])

    def test_sorts_by_date_ascending(self):
        """
        목적: 일자를 오름차순으로 정규화한다 (이후 계산이 정렬을 전제한다).

        Given: 일자가 내림차순인 시계열
        When: build_adjusted 호출
        Then: 오름차순으로 정렬된다
        """
        # Given
        series = _series(dates=["2019-01-03", "2019-01-02"], closes=[1060.0, 1050.0])

        # When
        result = build_adjusted(series, TICKER)

        # Then
        assert result["date"].tolist() == [pd.Timestamp("2019-01-02"), pd.Timestamp("2019-01-03")]
        assert result["close"].tolist() == pytest.approx([1050.0, 1060.0])

    def test_rejects_empty_series(self):
        """
        목적: 빈 조회 결과를 저장 대상으로 넘기지 않는다 (경계 조건).

        Given: 행이 없는 시계열
        When: build_adjusted 호출
        Then: ValueError가 발생한다
        """
        # Given
        series = _series().iloc[0:0]

        # When / Then
        with pytest.raises(ValueError, match="비어"):
            build_adjusted(series, TICKER)

    def test_rejects_missing_columns(self):
        """
        목적: 필수 컬럼이 빠진 결과를 조용히 통과시키지 않는다.

        Given: 거래량 컬럼이 없는 시계열
        When: build_adjusted 호출
        Then: ValueError가 발생한다
        """
        # Given
        series = _series().drop(columns=["거래량"])

        # When / Then
        with pytest.raises(ValueError, match="컬럼"):
            build_adjusted(series, TICKER)

    def test_rejects_non_datetime_index(self):
        """
        목적: pykrx 반환 형식이 바뀌면 즉시 인지한다.

        Given: 인덱스가 문자열인 시계열
        When: build_adjusted 호출
        Then: ValueError가 발생한다
        """
        # Given
        series = _series()
        series.index = pd.Index(["2019-01-02", "2019-01-03"])

        # When / Then
        with pytest.raises(ValueError, match="인덱스"):
            build_adjusted(series, TICKER)

    def test_rejects_duplicated_dates(self):
        """
        목적: 같은 일자가 두 번 오는 결과를 저장하지 않는다.

        Given: 일자가 중복된 시계열
        When: build_adjusted 호출
        Then: ValueError가 발생한다
        """
        # Given
        series = _series(dates=["2019-01-02", "2019-01-02"])

        # When / Then
        with pytest.raises(ValueError, match="중복"):
            build_adjusted(series, TICKER)

    def test_rejects_missing_values(self):
        """
        목적: 결측치를 보간하지 않고 즉시 예외로 막는다 (보간 금지).

        Given: 종가에 결측이 있는 시계열
        When: build_adjusted 호출
        Then: ValueError가 발생한다
        """
        # Given
        series = _series(closes=[1050.0, float("nan")])

        # When / Then
        with pytest.raises(ValueError, match="결측"):
            build_adjusted(series, TICKER)

    def test_rejects_invalid_ticker(self):
        """
        목적: 티커 형식을 입력 시점에 검증한다 (경계 조건).

        Given: 6자리가 아닌 티커
        When: build_adjusted 호출
        Then: ValueError가 발생한다
        """
        # Given / When / Then
        with pytest.raises(ValueError, match="티커"):
            build_adjusted(_series(), "5930")

    @pytest.mark.parametrize("ticker", ["0001A0", "03473K", "08537M"])
    def test_accepts_alphanumeric_ticker(self, ticker: str):
        """
        목적: 영문이 섞인 티커를 거부하지 않는다 (회귀 방지).

        KRX 티커는 숫자 전용이 아니다 — 신형 우선주(03473K)와 2025-07 이후 신규
        종목코드(0001A0)가 영문을 포함하며, 실측 유니버스 3,135종목 중 78종목이 이 형태다.

        Given: 영문이 포함된 6자리 티커
        When: build_adjusted 호출
        Then: 예외 없이 변환된다
        """
        # Given / When
        result = build_adjusted(_series(), ticker)

        # Then
        assert list(result.columns) == ADJUSTED_COLUMNS

    @pytest.mark.parametrize("ticker", ["00593a", "00593-", "00593가"])
    def test_rejects_out_of_charset_ticker(self, ticker: str):
        """
        목적: 소문자·기호·한글은 티커로 받지 않는다 (경계 조건).

        Given: 허용 문자 집합을 벗어난 6자리 문자열
        When: build_adjusted 호출
        Then: ValueError가 발생한다
        """
        # Given / When / Then
        with pytest.raises(ValueError, match="티커"):
            build_adjusted(_series(), ticker)

    def test_does_not_mutate_input(self):
        """
        목적: 원본 DataFrame을 변경하지 않는다 (데이터 불변성).

        Given: pykrx 형태의 시계열
        When: build_adjusted 호출
        Then: 입력의 컬럼 구성이 그대로 유지된다
        """
        # Given
        series = _series()
        before = list(series.columns)

        # When
        build_adjusted(series, TICKER)

        # Then
        assert list(series.columns) == before


class TestValidateAdjusted:
    """저장 직전 이상치 판정 정책을 고정한다 (스펙 §8)."""

    def test_passes_normal_series(self):
        """
        목적: 정상 시계열에는 경고가 없다.

        Given: 정상 시계열
        When: validate_adjusted 호출
        Then: 경고가 비어 있다
        """
        # Given / When
        warnings = validate_adjusted(_adjusted())

        # Then
        assert warnings == ()

    def test_rejects_negative_price(self):
        """
        목적: 음수 가격은 거래 여부와 무관하게 이상치다.

        Given: 저가가 음수인 시계열
        When: validate_adjusted 호출
        Then: ValueError가 발생한다
        """
        # Given
        frame = _adjusted()
        frame.loc[0, "low"] = -1.0

        # When / Then
        with pytest.raises(ValueError, match="음수"):
            validate_adjusted(frame)

    def test_rejects_zero_close_when_traded(self):
        """
        목적: 거래가 있는데 종가가 없으면 이상치다 (1단과 동일 정책).

        Given: 거래량이 있는데 종가가 0인 시계열
        When: validate_adjusted 호출
        Then: ValueError가 발생한다
        """
        # Given
        frame = _adjusted()
        frame.loc[0, "close"] = 0.0

        # When / Then
        with pytest.raises(ValueError, match="종가"):
            validate_adjusted(frame)

    def test_allows_zero_prices_when_not_traded(self):
        """
        목적: 거래정지일의 가격 0은 정상 패턴이다 (스펙 §8 실측).

        Given: 거래량 0이고 모든 가격이 0인 행
        When: validate_adjusted 호출
        Then: 예외 없이 경고도 없다
        """
        # Given
        frame = _adjusted()
        frame.loc[0, ["open", "high", "low", "close"]] = 0.0
        frame.loc[0, "volume"] = 0

        # When
        warnings = validate_adjusted(frame)

        # Then
        assert warnings == ()

    def test_warns_when_regular_session_missing(self):
        """
        목적: 거래는 있으나 고가·저가가 0인 행을 경고로 남긴다 (스윙 계산 제외 대상).

        Given: 거래량 > 0, 저가 0, 종가 정상인 행
        When: validate_adjusted 호출
        Then: 경고가 1건이다
        """
        # Given
        frame = _adjusted()
        frame.loc[0, ["open", "high", "low"]] = 0.0

        # When
        warnings = validate_adjusted(frame)

        # Then
        assert len(warnings) == 1

    def test_rejects_high_below_low(self):
        """
        목적: 고가가 저가보다 낮은 행은 이상치다.

        Given: 고가 < 저가인 시계열
        When: validate_adjusted 호출
        Then: ValueError가 발생한다
        """
        # Given
        frame = _adjusted()
        frame.loc[0, "high"] = 100.0

        # When / Then
        with pytest.raises(ValueError, match="고가"):
            validate_adjusted(frame)

    def test_rejects_missing_columns(self):
        """
        목적: 스키마가 어긋난 프레임을 검증 대상으로 받지 않는다 (경계 조건).

        Given: 컬럼이 빠진 프레임
        When: validate_adjusted 호출
        Then: ValueError가 발생한다
        """
        # Given
        frame = _adjusted().drop(columns=["volume"])

        # When / Then
        with pytest.raises(ValueError, match="컬럼"):
            validate_adjusted(frame)
