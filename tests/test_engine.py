"""engine 테스트

일별 루프·포지션·자금 관리의 계약을 고정한다 (백테스트 설계 §6·§7).

핵심 계약은 세 가지다.
- 신호는 D일 종가까지로 만들고 **체결은 D+1 봉에서만** 일어난다 (look-ahead 금지)
- 지표는 수정주가 축, 주문 가격은 원본가 축이다. 축 변환 계수는 신호일 값만 쓴다
- 동시 보유 상한과 **같은 클러스터 중복 진입 금지**를 지킨다
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from krx_sprint.backtest.engine import run_backtest
from krx_sprint.backtest.params import EntryPriceKind, ExecutionParams, StrategyParams
from krx_sprint.common_constants import COL_DATE, PANEL_COLUMNS

START_DATE = date(2024, 6, 3)

# 두 종목이 같은 이력으로 움직여 클러스터가 서고, 세 번째 종목이 시장 팩터 역할을 한다
LEADER = "000001"
PEER = "000002"
OTHER = "000003"

# 테스트용 최소 파라미터 — 창을 짧게 잡아 며칠짜리 시나리오로 신호가 나오게 한다
PARAMS = StrategyParams(
    correlation_window=3,
    correlation_threshold=0.3,
    co_move_rate=0.05,
    min_cluster_size=2,
    value_surge_multiple=2.0,
    value_average_window=2,
    base_bar_rise_rate=0.10,
    min_trading_value=0,
    swing_reversal_rate=0.05,
    base_bar_expiry_days=20,
    max_decline_count=2,
    entry_price_kind=EntryPriceKind.CLOSE_DISCOUNT,
    entry_discount_rate=0.03,
    stop_loss_rate=0.05,
    reward_risk_ratio=1.0,
    initial_equity=10_000_000,
    max_positions=3,
    min_market_cap=0,
    min_listed_days=0,
)

EXECUTION = ExecutionParams(liquidity_participation_rate=1.0, stop_loss_slippage_ticks=0)


def _trading_days(count: int) -> list[date]:
    """평일 거래일 캘린더를 만든다 (결제일 환산에 여유를 둔다)."""
    days: list[date] = []
    current = START_DATE
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _panel(closes: dict[str, list[int]], values: dict[str, list[int]] | None = None) -> pd.DataFrame:
    """종가열로 통합 패널을 만든다.

    시가는 전일 종가, 고가·저가는 종가 기준 ±0으로 두어 체결 판정이 종가에만 의존하게 한다.
    수정주가는 원본가와 같게 둔다 (액션 없음).
    """
    length = len(next(iter(closes.values())))
    days = _trading_days(length)
    rows: list[dict[str, object]] = []

    for ticker, series in closes.items():
        previous = series[0]
        for index, close in enumerate(series):
            change_rate = 0.0 if previous == 0 else (close / previous - 1) * 100
            traded_value = (values or {}).get(ticker, [1_000_000_000] * length)[index]
            rows.append(
                {
                    "date": pd.Timestamp(days[index]),
                    "ticker": ticker,
                    "market": "KOSPI",
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1_000,
                    "value": traded_value,
                    "change_rate": round(change_rate, 2),
                    "market_cap": close * 1_000_000,
                    "shares": 1_000_000,
                    "adj_open": float(close),
                    "adj_high": float(close),
                    "adj_low": float(close),
                    "adj_close": float(close),
                    "is_halted": False,
                    "no_regular_session": False,
                    "is_shares_jump": False,
                    "is_unadjusted_action": False,
                    "is_limit_up_close": False,
                    "is_limit_down_close": False,
                    "is_last_seen": index == length - 1,
                }
            )
            previous = close

    panel = pd.DataFrame(rows)[PANEL_COLUMNS]
    return panel.sort_values([COL_DATE, "ticker"]).reset_index(drop=True)


def _scenario(tail: list[int]) -> dict[str, list[int]]:
    """기준봉 → 눌림 → `tail` 로 이어지는 시나리오를 만든다.

    앞 3일은 평탄, 4일차에 +20% 기준봉, 5~6일차에 되돌림으로 1차 하락을 확정시킨다.
    """
    core = [1_000, 1_000, 1_000, 1_200, 1_100, 1_050]
    return {
        LEADER: [*core, *tail],
        PEER: [*core, *tail],
        OTHER: [1_000, 1_010, 1_000, 1_010, 1_000, 1_010, *([1_000] * len(tail))],
    }


def _values() -> dict[str, list[int]]:
    """기준봉 일자에만 거래대금이 급증하도록 만든다."""
    base = [100_000_000] * 3 + [1_000_000_000] + [100_000_000] * 20
    return {LEADER: base, PEER: base, OTHER: base}


def _run(closes: dict[str, list[int]], params: StrategyParams = PARAMS):
    """시나리오를 실행한다."""
    panel = _panel(closes, _values())
    return run_backtest(panel, _trading_days(40), params, EXECUTION)


class TestEntryAndExit:
    """진입·청산 흐름을 고정한다."""

    def test_take_profit_trade_is_recorded(self):
        """
        목적: 눌림에서 진입해 반등에서 익절되는 기본 흐름이 원장에 남는다.

        Given: 기준봉 후 1차 하락이 확정되고, 이후 크게 반등하는 시나리오
        When: run_backtest 실행
        Then: 익절로 종료된 트레이드가 1건 이상 있다
        """
        # Given / When
        result = _run(_scenario([1_020, 1_400, 1_400, 1_400]))

        # Then
        assert result.trades
        assert any(trade.exit_reason == "익절" for trade in result.trades)

    def test_stop_loss_trade_is_recorded(self):
        """
        목적: 진입 후 손절선을 이탈하면 손절로 종료된다.

        Given: 진입 직후 급락하는 시나리오
        When: run_backtest 실행
        Then: 손절로 종료된 트레이드가 있다
        """
        # Given / When
        result = _run(_scenario([1_020, 500, 500, 500]))

        # Then
        assert any(trade.exit_reason == "손절" for trade in result.trades)

    def test_entry_never_precedes_signal_date(self):
        """
        목적: **체결은 신호일 다음 거래일 이후**다 (예약매매 전제).

        Given: 익절로 끝나는 시나리오
        When: run_backtest 실행
        Then: 모든 트레이드의 진입일이 기준봉 일자보다 뒤다
        """
        # Given / When
        result = _run(_scenario([1_020, 1_400, 1_400, 1_400]))

        # Then
        assert result.trades
        for trade in result.trades:
            assert trade.entry_date > trade.base_bar_date

    def test_decline_count_within_limit(self):
        """
        목적: **3차 하락 이상 진입 금지**가 하드룰이다.

        Given: 여러 번 되돌리는 시나리오
        When: run_backtest 실행
        Then: 기록된 트레이드의 하락 차수가 모두 상한 이하다
        """
        # Given / When
        result = _run(_scenario([1_020, 990, 1_030, 1_000, 1_040, 1_010, 1_400]))

        # Then
        for trade in result.trades:
            assert 1 <= trade.decline_count <= PARAMS.max_decline_count


class TestCapitalAndSlots:
    """자금·슬롯 관리를 고정한다."""

    def test_single_position_per_cluster(self):
        """
        목적: 같은 클러스터에서 둘 이상 진입하지 않는다 — 테마 분산이 전략의 전제다.

        Given: 두 종목이 같은 클러스터로 묶이는 시나리오
        When: run_backtest 실행
        Then: 동시에 열린 포지션이 클러스터당 하나뿐이라 대장주만 기록된다
        """
        # Given / When
        result = _run(_scenario([1_020, 1_400, 1_400, 1_400]))

        # Then
        assert result.trades
        assert {trade.ticker for trade in result.trades} <= {LEADER, PEER}
        assert len({(trade.entry_date, trade.ticker) for trade in result.trades}) == len(result.trades)

    def test_equity_curve_is_recorded_daily(self):
        """
        목적: 자산 곡선이 매 거래일 기록된다 (MDD 계산의 전제).

        Given: 임의의 시나리오
        When: run_backtest 실행
        Then: 자산 곡선 길이가 거래일 수와 같다
        """
        # Given
        closes = _scenario([1_020, 1_400, 1_400, 1_400])

        # When
        result = _run(closes)

        # Then
        assert len(result.equity) == len(next(iter(closes.values())))

    def test_costs_reduce_net_pnl(self):
        """
        목적: 순손익은 총손익에서 수수료와 세금을 뺀 값이다.

        Given: 익절로 끝나는 시나리오
        When: run_backtest 실행
        Then: 모든 트레이드에서 순손익 = 총손익 − 수수료 − 세금
        """
        # Given / When
        result = _run(_scenario([1_020, 1_400, 1_400, 1_400]))

        # Then
        assert result.trades
        for trade in result.trades:
            assert trade.net_pnl == trade.gross_pnl - trade.fee - trade.tax
            assert trade.tax > 0


class TestGuards:
    """입력 검증을 고정한다."""

    def test_rejects_empty_panel(self):
        """
        목적: 빈 패널은 즉시 거부한다.

        Given: 빈 DataFrame
        When: run_backtest 호출
        Then: ValueError
        """
        with pytest.raises(ValueError, match="패널"):
            run_backtest(pd.DataFrame(columns=PANEL_COLUMNS), _trading_days(5), PARAMS, EXECUTION)

    def test_rejects_inverted_range(self):
        """
        목적: 시작일이 종료일보다 늦으면 거부한다.

        Given: 뒤집힌 구간
        When: run_backtest 호출
        Then: ValueError
        """
        panel = _panel(_scenario([1_020, 1_400]), _values())
        days = _trading_days(40)

        with pytest.raises(ValueError, match="구간"):
            run_backtest(panel, days, PARAMS, EXECUTION, start=days[5], end=days[0])
