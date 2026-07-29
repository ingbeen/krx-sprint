"""execution 테스트

일봉 예약매매의 체결 가정을 고정한다 (백테스트 설계 §7).

일봉만으로는 장중 순서를 알 수 없으므로 가정은 언제나 **불리한 쪽**으로 잡는다.
핵심 계약은 네 가지다.
- 갭은 우리에게 유리하든 불리하든 시가로 체결한다
- 손절과 익절이 같은 봉에서 모두 닿으면 **손절 우선**
- 거래정지·상한가 마감·하한가 마감·액션일에는 체결이 없다
- 손절은 장중 스톱이며 호가 1틱만큼 불리하게 체결된다
"""

from datetime import date

import pytest

from krx_sprint.backtest.execution import (
    DailyBar,
    ExitReason,
    fill_buy_limit,
    fill_sell_limit,
    fill_stop_loss,
    is_tradable,
    max_quantity,
    resolve_exit,
)
from krx_sprint.backtest.params import ExecutionParams

TRADE_DATE = date(2024, 6, 3)


def _bar(
    open_price: int = 10_000,
    high: int = 10_500,
    low: int = 9_500,
    close: int = 10_000,
    value: int = 1_000_000_000,
    market: str = "KOSPI",
    **flags: bool,
) -> DailyBar:
    """체결 판정용 일봉을 만든다. 플래그는 기본이 모두 False다."""
    return DailyBar(
        open=open_price,
        high=high,
        low=low,
        close=close,
        value=value,
        market=market,
        is_halted=flags.get("is_halted", False),
        is_limit_up_close=flags.get("is_limit_up_close", False),
        is_limit_down_close=flags.get("is_limit_down_close", False),
        is_shares_jump=flags.get("is_shares_jump", False),
    )


class TestTradability:
    """매매 불가 조건을 고정한다."""

    def test_halted_bar_is_not_tradable(self):
        """
        목적: 거래정지일에는 진입도 청산도 불가능하다 (실측 3.61%).

        Given: 거래량 0으로 정지된 날
        When: is_tradable 호출
        Then: False
        """
        assert is_tradable(_bar(is_halted=True)) is False

    def test_action_day_is_not_tradable(self):
        """
        목적: 상장주식수 급변일은 원본가 축이 바뀌어 전일 기준 지정가가 의미를 잃는다.

        Given: 액션(분할·감자)이 있은 날
        When: is_tradable 호출
        Then: False
        """
        assert is_tradable(_bar(is_shares_jump=True)) is False

    def test_normal_bar_is_tradable(self):
        """
        목적: 플래그가 없으면 매매 가능하다.

        Given: 평범한 일봉
        When: is_tradable 호출
        Then: True
        """
        assert is_tradable(_bar()) is True


class TestBuyLimit:
    """매수 지정가 체결 판정 3분기를 고정한다."""

    def test_gap_down_fills_at_open(self):
        """
        목적: 시가가 지정가보다 낮으면 시가에 체결된다 — 유리한 갭은 그대로 인정한다.

        Given: 지정가 9,800원, 시가 9,500원
        When: fill_buy_limit 호출
        Then: 시가 9,500원에 체결
        """
        assert fill_buy_limit(_bar(open_price=9_500, low=9_300), 9_800) == 9_500

    def test_intraday_touch_fills_at_limit(self):
        """
        목적: 장중 저가가 지정가에 닿으면 지정가에 체결된다.

        Given: 지정가 9,800원, 시가 10,000원, 저가 9,700원
        When: fill_buy_limit 호출
        Then: 지정가 9,800원에 체결
        """
        assert fill_buy_limit(_bar(open_price=10_000, low=9_700), 9_800) == 9_800

    def test_no_touch_does_not_fill(self):
        """
        목적: 저가가 지정가에 못 닿으면 미체결이다.

        Given: 지정가 9,000원, 저가 9,500원
        When: fill_buy_limit 호출
        Then: None
        """
        assert fill_buy_limit(_bar(low=9_500), 9_000) is None

    def test_limit_up_close_blocks_buy(self):
        """
        목적: 상한가 마감일은 매수 미체결로 본다 — 테마 대장주에서 특히 잦다.

        Given: 저가가 지정가 아래인데 상한가 마감 플래그가 선 날
        When: fill_buy_limit 호출
        Then: None
        """
        assert fill_buy_limit(_bar(low=9_000, is_limit_up_close=True), 9_500) is None

    def test_halted_blocks_buy(self):
        """
        목적: 거래정지일에는 체결이 없다.

        Given: 정지된 날
        When: fill_buy_limit 호출
        Then: None
        """
        assert fill_buy_limit(_bar(is_halted=True), 9_800) is None


class TestSellLimit:
    """매도 지정가(익절) 체결 판정 3분기를 고정한다."""

    def test_gap_up_fills_at_open(self):
        """
        목적: 시가가 목표가보다 높으면 시가에 체결된다.

        Given: 목표가 10,500원, 시가 11,000원
        When: fill_sell_limit 호출
        Then: 시가 11,000원에 체결
        """
        assert fill_sell_limit(_bar(open_price=11_000, high=11_200), 10_500) == 11_000

    def test_intraday_touch_fills_at_limit(self):
        """
        목적: 장중 고가가 목표가에 닿으면 목표가에 체결된다.

        Given: 목표가 10,500원, 시가 10,000원, 고가 10,600원
        When: fill_sell_limit 호출
        Then: 목표가 10,500원에 체결
        """
        assert fill_sell_limit(_bar(open_price=10_000, high=10_600), 10_500) == 10_500

    def test_no_touch_does_not_fill(self):
        """
        목적: 고가가 목표가에 못 닿으면 미체결이다.

        Given: 목표가 11_000원, 고가 10,500원
        When: fill_sell_limit 호출
        Then: None
        """
        assert fill_sell_limit(_bar(high=10_500), 11_000) is None

    def test_limit_down_close_blocks_sell(self):
        """
        목적: 하한가 마감일은 매도 미체결로 본다.

        Given: 고가가 목표가 위인데 하한가 마감 플래그가 선 날
        When: fill_sell_limit 호출
        Then: None
        """
        assert fill_sell_limit(_bar(high=11_000, is_limit_down_close=True), 10_500) is None


class TestStopLoss:
    """장중 스톱 손절을 고정한다 (설계 §13-③ 확정)."""

    def test_intraday_touch_fills_at_stop_with_slippage(self):
        """
        목적: 저가가 손절가에 닿으면 당일 체결되며 1틱 불리하게 잡는다.

        Given: 손절가 9,500원(호가단위 10원), 저가 9,400원
        When: fill_stop_loss 호출
        Then: 9,490원에 체결
        """
        assert fill_stop_loss(_bar(low=9_400), 9_500, TRADE_DATE, ExecutionParams()) == 9_490

    def test_gap_down_fills_at_open(self):
        """
        목적: 시가가 이미 손절가 아래면 시가에 체결된다 — 스톱은 그 가격을 지켜주지 않는다.

        Given: 손절가 9,500원, 시가 9,000원
        When: fill_stop_loss 호출
        Then: 9,000원 기준으로 체결된다 (1틱 불리 적용)
        """
        assert fill_stop_loss(_bar(open_price=9_000, low=8_900), 9_500, TRADE_DATE, ExecutionParams()) == 8_990

    def test_no_touch_does_not_fill(self):
        """
        목적: 저가가 손절가 위면 손절되지 않는다.

        Given: 손절가 9,000원, 저가 9,500원
        When: fill_stop_loss 호출
        Then: None
        """
        assert fill_stop_loss(_bar(low=9_500), 9_000, TRADE_DATE, ExecutionParams()) is None

    def test_slippage_can_be_disabled(self):
        """
        목적: 슬리피지 틱 수는 민감도 파라미터다.

        Given: 슬리피지 0틱
        When: fill_stop_loss 호출
        Then: 손절가 그대로 체결된다
        """
        params = ExecutionParams(stop_loss_slippage_ticks=0)
        assert fill_stop_loss(_bar(low=9_400), 9_500, TRADE_DATE, params) == 9_500


class TestResolveExit:
    """청산 우선순위를 고정한다."""

    def test_stop_wins_when_both_touched(self):
        """
        목적: 손절과 익절이 같은 봉에서 모두 닿으면 **손절 우선**이다 (일봉으로는 순서를 알 수 없다).

        Given: 저가가 손절가 아래이고 고가가 목표가 위인 봉
        When: resolve_exit 호출
        Then: 손절로 판정된다
        """
        # Given
        bar = _bar(open_price=10_000, high=11_000, low=9_000)

        # When
        exit_fill = resolve_exit(bar, stop_price=9_500, target_price=10_500, trade_date=TRADE_DATE)

        # Then
        assert exit_fill is not None
        assert exit_fill.reason is ExitReason.STOP_LOSS

    def test_take_profit_when_only_target_touched(self):
        """
        목적: 목표가만 닿으면 익절로 판정한다.

        Given: 저가가 손절가 위, 고가가 목표가 위인 봉
        When: resolve_exit 호출
        Then: 익절로 판정된다
        """
        # Given
        bar = _bar(open_price=10_000, high=11_000, low=9_800)

        # When
        exit_fill = resolve_exit(bar, stop_price=9_500, target_price=10_500, trade_date=TRADE_DATE)

        # Then
        assert exit_fill is not None
        assert exit_fill.reason is ExitReason.TAKE_PROFIT
        assert exit_fill.price == 10_500

    def test_no_exit_when_neither_touched(self):
        """
        목적: 둘 다 안 닿으면 보유를 이어간다.

        Given: 손절가와 목표가 사이에서만 움직인 봉
        When: resolve_exit 호출
        Then: None
        """
        bar = _bar(open_price=10_000, high=10_200, low=9_800)
        assert resolve_exit(bar, stop_price=9_500, target_price=10_500, trade_date=TRADE_DATE) is None

    def test_halted_bar_cannot_exit(self):
        """
        목적: 거래정지 중에는 손절 조건을 충족해도 청산할 수 없다 (강제 보유).

        Given: 손절가 아래로 내려간 정지일
        When: resolve_exit 호출
        Then: None
        """
        bar = _bar(low=9_000, is_halted=True)
        assert resolve_exit(bar, stop_price=9_500, target_price=10_500, trade_date=TRADE_DATE) is None


class TestMaxQuantity:
    """유동성 상한을 고정한다 (설계 §7.4)."""

    def test_limited_by_budget(self):
        """
        목적: 배분 금액을 넘는 수량은 살 수 없다.

        Given: 예산 1,000,000원, 가격 10,000원, 거래대금이 충분한 봉
        When: max_quantity 호출
        Then: 100주
        """
        assert max_quantity(_bar(value=10_000_000_000), 10_000, 1_000_000, ExecutionParams()) == 100

    def test_limited_by_liquidity(self):
        """
        목적: 당일 거래대금의 일정 비중을 넘는 주문은 체결하지 않는다.

        Given: 거래대금 1,000만원(참여율 1% → 10만원), 가격 10,000원, 예산은 넉넉함
        When: max_quantity 호출
        Then: 10주로 제한된다
        """
        assert max_quantity(_bar(value=10_000_000), 10_000, 1_000_000_000, ExecutionParams()) == 10

    def test_returns_zero_when_unaffordable(self):
        """
        목적: 한 주도 못 사면 0을 돌려준다 (음수·소수 금지).

        Given: 예산이 가격보다 적음
        When: max_quantity 호출
        Then: 0
        """
        assert max_quantity(_bar(), 10_000, 5_000, ExecutionParams()) == 0

    def test_rejects_non_positive_price(self):
        """
        목적: 가격 0 이하는 계산 대상이 아니다.

        Given: 가격 0
        When: max_quantity 호출
        Then: ValueError
        """
        with pytest.raises(ValueError, match="가격"):
            max_quantity(_bar(), 0, 1_000_000, ExecutionParams())
