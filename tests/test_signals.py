"""signals 테스트

기준봉·스윙·N차 하락 카운팅의 계약을 고정한다 (백테스트 설계 §5).

핵심 계약은 네 가지다.
- 기준봉 거래대금 평균 창에 **당일을 포함하지 않는다**. 포함하면 조건이 자기 자신에 희석된다
- 스윙 하이는 **이후 반전이 확인된 시점에 확정**된다. 확정 전에 쓰면 미래 정보 오염이다
- 신고가를 갱신하면 하락 카운터가 리셋된다 (설계 §13-② 확정)
- 다음 거래일 이동평균은 **신호일 종가가 이어진다는 가정**으로 채운다. 다음 날 종가는 아직 없다
"""

import pandas as pd
import pytest

from krx_sprint.backtest.params import StrategyParams
from krx_sprint.backtest.signals import (
    SwingKind,
    band_split_prices,
    count_declines,
    find_confirmed_swings,
    mark_base_bars,
    next_day_moving_average,
    swing_mask,
)
from krx_sprint.common_constants import (
    COL_ADJ_CLOSE,
    COL_CHANGE_RATE,
    COL_IS_HALTED,
    COL_IS_SHARES_JUMP,
    COL_IS_UNADJUSTED_ACTION,
    COL_NO_REGULAR_SESSION,
    COL_VALUE,
)


def _series(
    values: list[int],
    change_rates: list[float],
    shares_jumps: list[bool] | None = None,
    halted: list[bool] | None = None,
) -> pd.DataFrame:
    """기준봉 판정에 필요한 최소 시계열을 만든다."""
    size = len(values)
    return pd.DataFrame(
        {
            COL_VALUE: values,
            COL_CHANGE_RATE: change_rates,
            COL_IS_SHARES_JUMP: shares_jumps if shares_jumps is not None else [False] * size,
            COL_IS_HALTED: halted if halted is not None else [False] * size,
        }
    )


class TestFindConfirmedSwings:
    """ZigZag 스윙의 확정 시점을 고정한다."""

    def test_swing_high_is_confirmed_after_reversal(self):
        """
        목적: 스윙 하이는 **이후 r% 하락이 확인된 시점**에 확정된다.

        Given: 100 → 120으로 오른 뒤 108로 밀린 종가열 (반전 임계 5%)
        When: find_confirmed_swings 호출
        Then: 고점은 위치 2(120)이지만 확정 위치는 3이다
        """
        # Given / When
        swings = find_confirmed_swings([100.0, 110.0, 120.0, 108.0], 0.05)

        # Then
        assert len(swings) == 1
        assert swings[0].kind is SwingKind.HIGH
        assert swings[0].position == 2
        assert swings[0].price == pytest.approx(120.0)
        assert swings[0].confirmed_position == 3

    def test_unconfirmed_swing_is_not_reported(self):
        """
        목적: 반전이 임계에 못 미치면 스윙으로 확정하지 않는다.

        Given: 고점 120에서 118로만 밀린 종가열 (1.7% 하락)
        When: find_confirmed_swings 호출
        Then: 확정된 스윙이 없다
        """
        assert find_confirmed_swings([100.0, 110.0, 120.0, 118.0], 0.05) == ()

    def test_alternating_swings(self):
        """
        목적: 고점과 저점이 번갈아 확정된다.

        Given: 오르고 → 밀리고 → 다시 오르는 종가열
        When: find_confirmed_swings 호출
        Then: 고점·저점이 순서대로 확정된다
        """
        # Given / When
        swings = find_confirmed_swings([100.0, 120.0, 108.0, 100.0, 106.0], 0.05)

        # Then
        assert [swing.kind for swing in swings] == [SwingKind.HIGH, SwingKind.LOW]
        assert swings[1].position == 3
        assert swings[1].confirmed_position == 4

    def test_confirmation_never_precedes_occurrence(self):
        """
        목적: 확정 위치는 언제나 발생 위치보다 뒤다 (look-ahead 방지의 최소 불변조건).

        Given: 여러 번 반전하는 종가열
        When: find_confirmed_swings 호출
        Then: 모든 스윙에서 confirmed_position > position
        """
        # Given / When
        swings = find_confirmed_swings([100.0, 130.0, 110.0, 140.0, 115.0, 150.0], 0.05)

        # Then
        assert swings
        for swing in swings:
            assert swing.confirmed_position > swing.position

    def test_rejects_non_positive_reversal_rate(self):
        """
        목적: 반전 임계 0은 모든 등락을 스윙으로 만들어 의미가 없다.

        Given: 임계 0
        When: find_confirmed_swings 호출
        Then: ValueError
        """
        with pytest.raises(ValueError, match="반전"):
            find_confirmed_swings([100.0, 110.0], 0.0)


class TestCountDeclines:
    """N차 하락 카운팅과 리셋 정책을 고정한다."""

    def test_first_decline_is_counted_at_confirmation(self):
        """
        목적: 기준봉 이후 최초 확정 스윙 하이에서 1차 하락이 시작된다.

        Given: 기준봉(위치 0) 이후 고점을 찍고 밀린 종가열
        When: count_declines 호출
        Then: 확정 시점부터 카운트가 1이다
        """
        # Given
        params = StrategyParams(swing_reversal_rate=0.05)

        # When
        counts = count_declines([100.0, 120.0, 108.0], base_bar_position=0, params=params)

        # Then
        assert counts == (0, 0, 1)

    def test_second_decline_after_rebound(self):
        """
        목적: 반등 후 재하락은 2차로 센다.

        Given: 고점 → 하락 → 반등(신고가 미달) → 재하락
        When: count_declines 호출
        Then: 마지막 확정 시점의 카운트가 2다
        """
        # Given
        params = StrategyParams(swing_reversal_rate=0.05)

        # When — 120 고점 후 108로 하락(1차), 116으로 반등(신고가 미달), 104로 재하락(2차)
        counts = count_declines([100.0, 120.0, 108.0, 116.0, 104.0], base_bar_position=0, params=params)

        # Then
        assert counts[-1] == 2

    def test_new_high_resets_counter(self):
        """
        목적: 신고가를 갱신하면 카운터가 0으로 리셋된다 (설계 §13-② 확정).

        Given: 1차 하락 후 직전 고점을 넘어서는 반등
        When: count_declines 호출
        Then: 신고가 위치에서 카운트가 0이 된다
        """
        # Given
        params = StrategyParams(swing_reversal_rate=0.05)

        # When — 120 고점 → 108 하락(1차) → 130 신고가
        counts = count_declines([100.0, 120.0, 108.0, 130.0], base_bar_position=0, params=params)

        # Then
        assert counts[2] == 1
        assert counts[3] == 0

    def test_positions_before_base_bar_are_zero(self):
        """
        목적: 기준봉 이전 구간은 카운팅 대상이 아니다.

        Given: 기준봉이 위치 2인 종가열
        When: count_declines 호출
        Then: 앞 구간이 모두 0이다
        """
        # Given
        params = StrategyParams(swing_reversal_rate=0.05)

        # When
        counts = count_declines([130.0, 100.0, 100.0, 120.0, 108.0], base_bar_position=2, params=params)

        # Then
        assert counts[0] == 0
        assert counts[1] == 0

    def test_expiry_stops_counting(self):
        """
        목적: 기준봉 유효기간이 지나면 그 기준봉은 무효다.

        Given: 유효기간 2거래일인 파라미터
        When: 기준봉에서 3거래일 이상 떨어진 위치를 확인
        Then: 카운트가 0이다
        """
        # Given
        params = StrategyParams(swing_reversal_rate=0.05, base_bar_expiry_days=2)

        # When
        counts = count_declines([100.0, 120.0, 108.0, 116.0, 104.0], base_bar_position=0, params=params)

        # Then
        assert counts[-1] == 0

    def test_invalidation_below_base_open(self):
        """
        목적: 기준봉 시가를 이탈하면 그 기준봉은 무효가 된다.

        Given: 무효화 기준선 95
        When: 종가가 그 아래로 내려간 이후 위치를 확인
        Then: 카운트가 0이다
        """
        # Given
        params = StrategyParams(swing_reversal_rate=0.05)

        # When — 90으로 이탈한 뒤 다시 오르내려도 무효 상태를 유지한다
        counts = count_declines(
            [100.0, 120.0, 90.0, 120.0, 108.0],
            base_bar_position=0,
            params=params,
            invalidate_below=95.0,
        )

        # Then
        assert counts[-1] == 0


class TestMarkBaseBars:
    """기준봉 판정을 고정한다."""

    def test_average_window_excludes_current_day(self):
        """
        목적: 거래대금 평균 창에 **당일을 포함하지 않는다**.

        Given: 과거 2일 평균이 100인데 당일 거래대금이 400인 시계열 (배수 3배 기준)
        When: mark_base_bars 호출
        Then: 당일이 기준봉이다 — 당일을 평균에 넣었다면 평균이 200이 돼 탈락했을 것이다
        """
        # Given
        params = StrategyParams(
            value_surge_multiple=3.0, value_average_window=2, base_bar_rise_rate=0.10, min_trading_value=0
        )
        series = _series(values=[100, 100, 400], change_rates=[0.0, 0.0, 15.0])

        # When
        marked = mark_base_bars(series, params)

        # Then
        assert bool(marked.iloc[2]) is True

    def test_requires_rise_rate(self):
        """
        목적: 거래대금이 급증해도 상승률 조건을 못 채우면 기준봉이 아니다.

        Given: 거래대금은 4배지만 등락률이 +1%인 날
        When: mark_base_bars 호출
        Then: 기준봉이 아니다
        """
        # Given
        params = StrategyParams(
            value_surge_multiple=3.0, value_average_window=2, base_bar_rise_rate=0.10, min_trading_value=0
        )
        series = _series(values=[100, 100, 400], change_rates=[0.0, 0.0, 1.0])

        # When / Then
        assert bool(mark_base_bars(series, params).iloc[2]) is False

    def test_requires_minimum_trading_value(self):
        """
        목적: 거래대금 하한이 실질적인 종목 필터다 (스펙 §0 — 기준봉만으로는 종목이 안 걸러진다).

        Given: 배수·상승률은 충족하지만 거래대금이 하한 미만인 날
        When: mark_base_bars 호출
        Then: 기준봉이 아니다
        """
        # Given
        params = StrategyParams(value_surge_multiple=3.0, value_average_window=2, min_trading_value=1_000)
        series = _series(values=[100, 100, 400], change_rates=[0.0, 0.0, 15.0])

        # When / Then
        assert bool(mark_base_bars(series, params).iloc[2]) is False

    def test_shares_jump_day_is_excluded(self):
        """
        목적: 상장주식수 급변일은 등락률이 왜곡되므로 기준봉 판정에서 제외한다 (최대 +29,948%).

        Given: 조건을 모두 충족하지만 액션이 있은 날
        When: mark_base_bars 호출
        Then: 기준봉이 아니다
        """
        # Given
        params = StrategyParams(value_surge_multiple=3.0, value_average_window=2, min_trading_value=0)
        series = _series(
            values=[100, 100, 400],
            change_rates=[0.0, 0.0, 15.0],
            shares_jumps=[False, False, True],
        )

        # When / Then
        assert bool(mark_base_bars(series, params).iloc[2]) is False

    def test_halted_day_is_excluded(self):
        """
        목적: 거래정지일은 기준봉이 될 수 없다.

        Given: 거래정지 플래그가 선 날
        When: mark_base_bars 호출
        Then: 기준봉이 아니다
        """
        # Given
        params = StrategyParams(value_surge_multiple=3.0, value_average_window=2, min_trading_value=0)
        series = _series(
            values=[100, 100, 400],
            change_rates=[0.0, 0.0, 15.0],
            halted=[False, False, True],
        )

        # When / Then
        assert bool(mark_base_bars(series, params).iloc[2]) is False

    def test_early_rows_without_full_window_are_not_base_bars(self):
        """
        목적: 평균 창을 채우지 못한 초반 구간은 판정하지 않는다 (결측을 조건 통과로 취급 금지).

        Given: 창 길이 2인데 첫 행
        When: mark_base_bars 호출
        Then: 기준봉이 아니다
        """
        # Given
        params = StrategyParams(value_surge_multiple=3.0, value_average_window=2, min_trading_value=0)
        series = _series(values=[400, 100, 100], change_rates=[15.0, 0.0, 0.0])

        # When / Then
        assert bool(mark_base_bars(series, params).iloc[0]) is False


class TestNextDayMovingAverage:
    """다음 거래일 이동평균의 정의를 고정한다."""

    def test_last_slot_is_filled_with_signal_day_close(self):
        """
        목적: 창의 마지막 칸을 **신호일 종가**로 채운다 — 다음 날 종가는 아직 없다.

        Given: 종가열 9500·9700·10200·10000 (마지막이 신호일)
        When: 5일 이동평균 요청
        Then: (9500+9700+10200+10000+10000)/5 = 9880
        """
        # Given
        prices = [9_500.0, 9_700.0, 10_200.0, 10_000.0]

        # When
        average = next_day_moving_average(prices, 3, 5)

        # Then
        assert average == pytest.approx(9_880.0)

    def test_future_rows_do_not_change_the_average(self):
        """
        목적: **look-ahead 감시** — 신호일 뒤에 값이 더 붙어도 이동평균이 달라지면 안 된다.

        Given: 같은 신호일 위치에 미래 구간이 덧붙은 종가열
        When: 두 입력으로 각각 계산
        Then: 결과가 같다
        """
        # Given
        prices = [9_500.0, 9_700.0, 10_200.0, 10_000.0]
        extended = [*prices, 5_000.0, 20_000.0]

        # When / Then
        assert next_day_moving_average(prices, 3, 5) == next_day_moving_average(extended, 3, 5)

    def test_returns_none_when_window_is_unfilled(self):
        """
        목적: 창을 채우지 못하면 값을 주지 않는다 (결측을 0으로 메우지 않는다).

        Given: 종가 두 개뿐인 구간
        When: 20일 이동평균 요청
        Then: None
        """
        assert next_day_moving_average([1_000.0, 1_010.0], 1, 20) is None

    def test_returns_none_when_halted_price_is_in_window(self):
        """
        목적: 0 이하 종가(거래정지)가 창에 섞이면 평균이 의미를 잃는다.

        Given: 창 안에 0이 있는 종가열
        When: 이동평균 요청
        Then: None
        """
        assert next_day_moving_average([1_000.0, 0.0, 1_020.0], 2, 3) is None


class TestBandSplitPrices:
    """이동평균 밴드의 분할 지정가를 고정한다."""

    def test_interior_points_split_each_band_evenly(self):
        """
        목적: 각 밴드를 균등 분할한 **내부점**을 쓴다 — 선 위에서 받치는 주문은 이 전략의 자리가 아니다.

        Given: 경계 9880·9450·8900 과 밴드당 2개
        When: band_split_prices 호출
        Then: 밴드마다 3등분 지점 두 개씩 총 네 개가 내림차순으로 나온다
        """
        # When
        prices = band_split_prices([9_880.0, 9_450.0, 8_900.0], 2)

        # Then
        assert prices == pytest.approx((9_736.666667, 9_593.333333, 9_266.666667, 9_083.333333))

    def test_inverted_bounds_produce_no_price(self):
        """
        목적: 짧은 이동평균이 긴 것보다 아래면(역배열) 매수 자리를 만들지 않는다 —
        눌림목이 아니라 추세 하락이다.

        Given: 오름차순으로 뒤집힌 경계
        When: band_split_prices 호출
        Then: 빈 튜플
        """
        assert band_split_prices([8_900.0, 9_450.0, 9_880.0], 2) == ()

    def test_rejects_single_bound(self):
        """
        목적: 경계가 하나면 밴드가 성립하지 않는다.

        Given: 경계 한 개
        When: band_split_prices 호출
        Then: ValueError
        """
        with pytest.raises(ValueError, match="밴드 경계"):
            band_split_prices([9_880.0], 2)


class TestSwingMask:
    """스윙·고저 계산에서 제외할 행을 고정한다."""

    def test_excludes_no_regular_session_and_unadjusted_and_halted(self):
        """
        목적: 정규장 미형성·수정 미반영·거래정지 행은 스윙 계산에서 빠진다 (스펙 §8.1·§8.2).

        Given: 세 플래그가 하나씩 선 시계열
        When: swing_mask 호출
        Then: 정상 행만 True다
        """
        # Given
        frame = pd.DataFrame(
            {
                COL_ADJ_CLOSE: [100.0, 100.0, 100.0, 100.0],
                COL_NO_REGULAR_SESSION: [False, True, False, False],
                COL_IS_UNADJUSTED_ACTION: [False, False, True, False],
                COL_IS_HALTED: [False, False, False, True],
            }
        )

        # When
        mask = swing_mask(frame)

        # Then
        assert list(mask) == [True, False, False, False]
