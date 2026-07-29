"""costs 테스트

비용 모델의 계약을 고정한다 (백테스트 설계 §8).

핵심 계약은 세 가지다.
- 증권거래세는 **결제일(체결일 + 2 거래일)** 기준으로 고른다. 경계일과 그 직전 거래일이
  서로 다른 세율을 받아야 한다 — 오프바이원이 가장 나기 쉬운 지점이다
- 호가가격단위는 2023-01-25 개편 전후가 다르고, 개편 전에는 시장별로도 다르다
- 매수는 수수료만, 매도는 수수료 + 거래세를 문다
"""

from datetime import date, timedelta

import pytest

from krx_sprint.backtest.costs import (
    DEFAULT_FEE_RATE,
    SETTLEMENT_LAG_DAYS,
    TICK_REFORM_DATE,
    CostModel,
    OrderSide,
    align_price,
    tick_size,
)

KOSPI = "KOSPI"
KOSDAQ = "KOSDAQ"

# 설계 §8.2에서 거래일 캘린더로 환산한 세율 경계.
# (최초 적용 체결일, 그 직전 체결일, 직전 구간 합계 세율, 신규 구간 합계 세율)
TAX_BOUNDARIES = [
    (date(2019, 5, 30), date(2019, 5, 29), 0.0030, 0.0025),
    (date(2020, 12, 29), date(2020, 12, 28), 0.0025, 0.0023),
    (date(2022, 12, 28), date(2022, 12, 27), 0.0023, 0.0020),
    (date(2023, 12, 27), date(2023, 12, 26), 0.0020, 0.0018),
    (date(2024, 12, 27), date(2024, 12, 26), 0.0018, 0.0015),
    (date(2025, 12, 29), date(2025, 12, 26), 0.0015, 0.0020),
]

# 세율 경계 주변의 **실제 KRX 거래일** (1단 스냅샷에서 추출).
# 결제일 환산은 연말 휴장에 좌우되므로 합성 평일 캘린더로는 경계를 재현할 수 없다 —
# 예컨대 2020-12-31과 2021-01-01은 휴장이라 2020-12-29 체결분의 결제일이 2021-01-04가 된다
REAL_TRADING_DAYS = [
    date(2019, 5, 27),
    date(2019, 5, 28),
    date(2019, 5, 29),
    date(2019, 5, 30),
    date(2019, 5, 31),
    date(2019, 6, 3),
    date(2019, 6, 4),
    date(2019, 6, 5),
    date(2020, 12, 24),
    date(2020, 12, 28),
    date(2020, 12, 29),
    date(2020, 12, 30),
    date(2021, 1, 4),
    date(2021, 1, 5),
    date(2021, 1, 6),
    date(2022, 12, 23),
    date(2022, 12, 26),
    date(2022, 12, 27),
    date(2022, 12, 28),
    date(2022, 12, 29),
    date(2023, 1, 2),
    date(2023, 1, 3),
    date(2023, 1, 4),
    date(2023, 1, 5),
    date(2023, 12, 22),
    date(2023, 12, 26),
    date(2023, 12, 27),
    date(2023, 12, 28),
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 12, 23),
    date(2024, 12, 24),
    date(2024, 12, 26),
    date(2024, 12, 27),
    date(2024, 12, 30),
    date(2025, 1, 2),
    date(2025, 1, 3),
    date(2025, 1, 6),
    date(2025, 12, 23),
    date(2025, 12, 24),
    date(2025, 12, 26),
    date(2025, 12, 29),
    date(2025, 12, 30),
    date(2026, 1, 2),
    date(2026, 1, 5),
    date(2026, 1, 6),
]


def _weekday_calendar(start: date, end: date) -> list[date]:
    """평일만 담은 거래일 캘린더를 만든다.

    실제 KRX 휴장일은 반영하지 않는다 — 경계 테스트는 실제 캘린더를 쓰는 별도 테스트에서 한다.
    """
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


class TestSettlementDate:
    """체결일 → 결제일 환산 규칙을 고정한다."""

    def test_skips_weekend(self):
        """
        목적: 결제일은 달력일이 아니라 **거래일** 기준 T+2다.

        Given: 평일만 담은 캘린더에서 금요일 체결
        When: settlement_date 호출
        Then: 다음 주 화요일이 결제일이다
        """
        # Given
        model = CostModel(_weekday_calendar(date(2026, 1, 1), date(2026, 3, 31)))

        # When
        settled = model.settlement_date(date(2026, 1, 16))  # 금요일

        # Then
        assert settled == date(2026, 1, 20)  # 화요일

    def test_lag_is_two_trading_days(self):
        """
        목적: 지연일수 상수와 실제 동작이 일치한다.

        Given: 연속된 평일 캘린더
        When: 첫 체결일의 결제일을 구함
        Then: 캘린더에서 SETTLEMENT_LAG_DAYS 만큼 뒤의 거래일이다
        """
        # Given
        days = _weekday_calendar(date(2026, 1, 1), date(2026, 3, 31))
        model = CostModel(days)

        # When
        settled = model.settlement_date(days[0])

        # Then
        assert settled == days[SETTLEMENT_LAG_DAYS]

    def test_rejects_non_trading_day(self):
        """
        목적: 거래일이 아닌 일자는 즉시 거부한다 (암묵적 보정 금지).

        Given: 평일 캘린더
        When: 토요일을 체결일로 전달
        Then: ValueError
        """
        # Given
        model = CostModel(_weekday_calendar(date(2026, 1, 1), date(2026, 3, 31)))

        # When / Then
        with pytest.raises(ValueError, match="거래일"):
            model.settlement_date(date(2026, 1, 17))  # 토요일

    def test_rejects_when_settlement_beyond_calendar(self):
        """
        목적: 결제일이 캘린더 밖이면 세율을 확정할 수 없으므로 거부한다.

        Given: 캘린더의 마지막 거래일
        When: settlement_date 호출
        Then: ValueError
        """
        # Given
        days = _weekday_calendar(date(2026, 1, 1), date(2026, 1, 30))
        model = CostModel(days)

        # When / Then
        with pytest.raises(ValueError, match="결제일"):
            model.settlement_date(days[-1])


class TestSecuritiesTaxRate:
    """기간·시장별 증권거래세율을 고정한다."""

    @pytest.fixture
    def model(self) -> CostModel:
        """실제 KRX 거래일이 아닌 평일 캘린더로도 경계 검증이 가능하도록 넓은 구간을 준다."""
        return CostModel(_weekday_calendar(date(2019, 1, 1), date(2026, 12, 31)))

    @pytest.mark.parametrize("first_trade,previous_trade,old_rate,new_rate", TAX_BOUNDARIES)
    def test_boundary_by_settlement_date(self, first_trade, previous_trade, old_rate, new_rate):
        """
        목적: 세율 경계는 체결일이 아니라 결제일 기준이다 (설계 §8.2).

        Given: 실제 KRX 거래일 캘린더와 경계 체결일·그 직전 체결일
        When: 각각의 세율을 조회
        Then: 직전 체결일은 구세율, 경계 체결일은 신세율을 받는다
        """
        # Given
        model = CostModel(REAL_TRADING_DAYS)

        # When / Then
        assert model.tax_rate(previous_trade, KOSPI) == pytest.approx(old_rate)
        assert model.tax_rate(first_trade, KOSPI) == pytest.approx(new_rate)

    @pytest.mark.parametrize("first_trade,previous_trade,old_rate,new_rate", TAX_BOUNDARIES)
    def test_boundary_holds_for_kosdaq(self, first_trade, previous_trade, old_rate, new_rate):
        """
        목적: 코스닥도 같은 경계에서 같은 합계 세율로 바뀐다 (설계 §8.1).

        Given: 실제 KRX 거래일 캘린더
        When: 코스닥 세율을 조회
        Then: 코스피와 같은 값이 나온다
        """
        # Given
        model = CostModel(REAL_TRADING_DAYS)

        # When / Then
        assert model.tax_rate(previous_trade, KOSDAQ) == pytest.approx(old_rate)
        assert model.tax_rate(first_trade, KOSDAQ) == pytest.approx(new_rate)

    def test_kospi_and_kosdaq_totals_match_in_sample(self, model: CostModel):
        """
        목적: 표본 전 구간에서 두 시장의 합계 세율이 같다 (설계 §8.1).

        Given: 각 세율 구간의 대표 체결일
        When: 시장별 세율 조회
        Then: 코스피 합계 = 코스닥 합계
        """
        # Given
        samples = [date(2019, 3, 4), date(2020, 6, 1), date(2022, 6, 1), date(2023, 6, 1), date(2026, 3, 2)]

        # When / Then
        for target in samples:
            assert model.tax_rate(target, KOSPI) == pytest.approx(model.tax_rate(target, KOSDAQ))

    def test_kospi_splits_into_base_and_surtax(self, model: CostModel):
        """
        목적: 코스피는 본세 + 농특세 0.15%로 분해되고 코스닥은 농특세가 없다.

        Given: 2026년 체결일
        When: 세율 구성 조회
        Then: 코스피는 0.05% + 0.15%, 코스닥은 0.20% + 0%
        """
        # Given / When
        kospi = model.tax_components(date(2026, 3, 2), KOSPI)
        kosdaq = model.tax_components(date(2026, 3, 2), KOSDAQ)

        # Then
        assert kospi.base_rate == pytest.approx(0.0005)
        assert kospi.surtax_rate == pytest.approx(0.0015)
        assert kosdaq.base_rate == pytest.approx(0.0020)
        assert kosdaq.surtax_rate == pytest.approx(0.0)

    def test_rejects_unknown_market(self, model: CostModel):
        """
        목적: 유니버스 밖 시장은 세율을 임의로 추정하지 않는다.

        Given: 코넥스
        When: 세율 조회
        Then: ValueError
        """
        # When / Then
        with pytest.raises(ValueError, match="시장"):
            model.tax_rate(date(2024, 6, 3), "KONEX")


class TestTickSize:
    """호가가격단위 테이블을 고정한다 (설계 §8.3)."""

    @pytest.mark.parametrize(
        "price,expected",
        [(999, 1), (1_000, 5), (4_999, 5), (5_000, 10), (10_000, 50), (50_000, 100), (100_000, 500), (500_000, 1_000)],
    )
    def test_legacy_kospi(self, price, expected):
        """
        목적: 개편 전 코스피 호가단위를 구간 경계까지 고정한다.

        Given: 2023-01-25 이전 체결일
        When: tick_size 호출
        Then: 개편 전 코스피 표와 일치한다
        """
        assert tick_size(price, KOSPI, date(2023, 1, 24)) == expected

    def test_legacy_kosdaq_differs_above_100k(self):
        """
        목적: 개편 전에는 10만~50만원 구간에서 시장별 호가단위가 달랐다.

        Given: 2023-01-25 이전 체결일, 가격 150,000원
        When: 시장별 tick_size 호출
        Then: 코스피 500원, 코스닥 100원
        """
        # Given / When / Then
        assert tick_size(150_000, KOSPI, date(2023, 1, 24)) == 500
        assert tick_size(150_000, KOSDAQ, date(2023, 1, 24)) == 100

    @pytest.mark.parametrize(
        "price,expected",
        [(1_999, 1), (2_000, 5), (4_999, 5), (5_000, 10), (19_999, 10), (20_000, 50), (199_999, 100), (200_000, 500)],
    )
    def test_current_table_applies_from_reform_date(self, price, expected):
        """
        목적: 개편일 당일부터 새 표를 쓰고, 개편 후에는 두 시장이 같다.

        Given: 2023-01-25 체결일
        When: tick_size 호출
        Then: 개편 후 표와 일치하며 코스피·코스닥이 동일하다
        """
        assert tick_size(price, KOSPI, TICK_REFORM_DATE) == expected
        assert tick_size(price, KOSDAQ, TICK_REFORM_DATE) == expected

    def test_rejects_non_positive_price(self):
        """
        목적: 가격 0 이하(거래정지일 등)는 호가단위를 정의할 수 없다.

        Given: 가격 0
        When: tick_size 호출
        Then: ValueError
        """
        with pytest.raises(ValueError, match="가격"):
            tick_size(0, KOSPI, date(2024, 6, 3))


class TestAlignPrice:
    """지정가 정렬 방향을 고정한다 — 언제나 불리한 쪽으로 맞춘다."""

    def test_buy_rounds_down(self):
        """
        목적: 매수 지정가는 내림 정렬해 체결 조건을 더 빡빡하게 만든다.

        Given: 호가단위 10원 구간의 어중간한 가격
        When: 매수 정렬
        Then: 아래쪽 호가로 맞춰진다
        """
        assert align_price(7_123, KOSPI, date(2024, 6, 3), side=OrderSide.BUY) == 7_120

    def test_sell_rounds_up(self):
        """
        목적: 매도 지정가는 올림 정렬해 체결 조건을 더 빡빡하게 만든다.

        Given: 호가단위 10원 구간의 어중간한 가격
        When: 매도 정렬
        Then: 위쪽 호가로 맞춰진다
        """
        assert align_price(7_123, KOSPI, date(2024, 6, 3), side=OrderSide.SELL) == 7_120 + 10

    def test_already_aligned_price_is_unchanged(self):
        """
        목적: 이미 호가에 맞는 가격은 방향과 무관하게 그대로다.

        Given: 호가단위의 배수인 가격
        When: 매수·매도 정렬
        Then: 둘 다 입력과 같다
        """
        assert align_price(7_120, KOSPI, date(2024, 6, 3), side=OrderSide.BUY) == 7_120
        assert align_price(7_120, KOSPI, date(2024, 6, 3), side=OrderSide.SELL) == 7_120

    def test_result_is_valid_in_its_own_band(self):
        """
        목적: 정렬 결과가 구간을 넘어가도 그 구간의 호가단위에 맞아야 한다.

        Given: 구간 경계 바로 아래 가격의 매도 정렬 (19,999 → 20,000)
        When: 결과 가격의 호가단위로 나눔
        Then: 나머지가 0이다
        """
        # Given / When
        aligned = align_price(19_999, KOSPI, date(2024, 6, 3), side=OrderSide.SELL)

        # Then
        assert aligned == 20_000
        assert aligned % tick_size(aligned, KOSPI, date(2024, 6, 3)) == 0


class TestTradeCost:
    """매수·매도 비용 계산을 고정한다."""

    @pytest.fixture
    def model(self) -> CostModel:
        return CostModel(_weekday_calendar(date(2026, 1, 1), date(2026, 12, 31)))

    def test_buy_has_no_tax(self, model: CostModel):
        """
        목적: 증권거래세는 매도에만 부과된다.

        Given: 매수 100주 × 10,000원
        When: buy_cost 호출
        Then: 세금 0, 수수료는 기본 요율 기준
        """
        # Given / When
        cost = model.buy_cost(10_000, 100)

        # Then — 1,000,000 × 0.00015 = 150
        assert cost.tax == 0
        assert cost.fee == 150
        assert cost.total == 150

    def test_sell_adds_tax(self, model: CostModel):
        """
        목적: 매도 비용은 수수료 + 거래세다.

        Given: 2026년 코스피 매도 100주 × 10,000원 (합계 세율 0.20%)
        When: sell_cost 호출
        Then: 수수료 150원 + 세금 2,000원
        """
        # Given / When
        cost = model.sell_cost(10_000, 100, date(2026, 3, 2), KOSPI)

        # Then
        assert cost.fee == 150
        assert cost.tax == 2_000
        assert cost.total == 2_150

    def test_costs_round_up(self, model: CostModel):
        """
        목적: 비용은 원 단위 올림으로 보수적으로 잡는다.

        Given: 수수료가 소수로 떨어지는 금액 (1주 × 1,001원 → 0.15015원)
        When: buy_cost 호출
        Then: 1원으로 올림된다
        """
        assert model.buy_cost(1_001, 1).fee == 1

    def test_rejects_invalid_quantity(self, model: CostModel):
        """
        목적: 수량 0 이하는 계산 대상이 아니다.

        Given: 수량 0
        When: buy_cost 호출
        Then: ValueError
        """
        with pytest.raises(ValueError, match="수량"):
            model.buy_cost(10_000, 0)

    def test_rejects_negative_fee_rate(self):
        """
        목적: 음수 수수료율은 즉시 거부한다.

        Given: 음수 요율
        When: CostModel 생성
        Then: ValueError
        """
        with pytest.raises(ValueError, match="수수료율"):
            CostModel(_weekday_calendar(date(2026, 1, 1), date(2026, 3, 31)), fee_rate=-0.1)

    def test_default_fee_rate_is_conservative(self):
        """
        목적: 기본 수수료율은 설계 §13-①에서 확정한 0.015%다.

        Given: 상수
        When: 값 확인
        Then: 비율 0.00015
        """
        assert DEFAULT_FEE_RATE == pytest.approx(0.00015)
