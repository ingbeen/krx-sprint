"""metrics 테스트

성과 지표를 손계산 가능한 소형 원장으로 고정한다 (백테스트 설계 §9.3).

핵심 계약은 두 가지다.
- **비용 전/후 수익을 나란히** 낸다. 단타 전략은 비용이 수익을 통째로 먹을 수 있다
- 트레이드가 하나도 없어도 예외 없이 요약이 나온다 (전략 기각도 정당한 결론이다)
"""

from datetime import date

import pytest

from krx_sprint.backtest.engine import BacktestResult, Trade
from krx_sprint.backtest.metrics import equity_frame, summarize, trades_frame

INITIAL_EQUITY = 10_000_000


def _trade(net_pnl: int, gross_pnl: int | None = None, exit_day: int = 5, holding_days: int = 2) -> Trade:
    """손계산이 쉬운 트레이드 한 건을 만든다."""
    gross = gross_pnl if gross_pnl is not None else net_pnl + 1_000
    return Trade(
        ticker="000001",
        market="KOSPI",
        cluster_size=2,
        base_bar_date=date(2024, 6, 3),
        decline_count=1,
        entry_date=date(2024, 6, 4),
        entry_price=10_000,
        quantity=100,
        exit_date=date(2024, 6, exit_day),
        exit_price=10_100,
        exit_reason="익절",
        gross_pnl=gross,
        fee=600,
        tax=400,
        net_pnl=net_pnl,
        holding_days=holding_days,
        invalidated=False,
    )


def _result(trades: list[Trade], equity: list[int]) -> BacktestResult:
    """자산 곡선과 원장을 갖춘 결과를 만든다."""
    curve = [(date(2024, 6, 3 + index), value, value) for index, value in enumerate(equity)]
    return BacktestResult(trades=trades, equity=curve)


class TestSummarize:
    """성과 지표 계산을 고정한다."""

    def test_total_return_and_final_equity(self):
        """
        목적: 누적 수익률은 자산 곡선의 처음과 끝으로 계산한다.

        Given: 1,000만 → 1,100만으로 끝나는 자산 곡선
        When: summarize 호출
        Then: 누적 수익률 10%
        """
        # Given
        result = _result([_trade(net_pnl=1_000_000)], [10_000_000, 10_500_000, 11_000_000])

        # When
        performance = summarize(result, INITIAL_EQUITY)

        # Then
        assert performance.final_equity == 11_000_000
        assert performance.total_return_pct == pytest.approx(10.0)

    def test_max_drawdown(self):
        """
        목적: 최대 낙폭은 고점 대비 최대 하락폭이다.

        Given: 1,200만까지 올랐다가 900만까지 내려간 곡선
        When: summarize 호출
        Then: 최대 낙폭이 −25%다
        """
        # Given
        result = _result([_trade(net_pnl=-1_000_000)], [10_000_000, 12_000_000, 9_000_000])

        # When
        performance = summarize(result, INITIAL_EQUITY)

        # Then
        assert performance.max_drawdown_pct == pytest.approx(-25.0)

    def test_win_rate_and_expectancy(self):
        """
        목적: 승률과 트레이드당 기대값을 정확히 센다.

        Given: 이익 1건 + 손실 1건
        When: summarize 호출
        Then: 승률 50%, 기대값은 두 순손익의 평균
        """
        # Given
        result = _result(
            [_trade(net_pnl=300_000), _trade(net_pnl=-100_000, exit_day=6)],
            [10_000_000, 10_200_000],
        )

        # When
        performance = summarize(result, INITIAL_EQUITY)

        # Then
        assert performance.trade_count == 2
        assert performance.win_rate_pct == pytest.approx(50.0)
        assert performance.expectancy == 100_000

    def test_cost_totals_are_reported(self):
        """
        목적: **비용 전/후 수익을 나란히** 낸다 — 이 전략의 생사가 여기 달려 있다.

        Given: 총손익 대비 비용이 큰 트레이드 2건
        When: summarize 호출
        Then: 총손익·순손익·수수료·세금이 모두 집계된다
        """
        # Given
        result = _result(
            [_trade(net_pnl=1_000, gross_pnl=2_000), _trade(net_pnl=-1_000, gross_pnl=0, exit_day=6)],
            [10_000_000, 10_000_000],
        )

        # When
        performance = summarize(result, INITIAL_EQUITY)

        # Then
        assert performance.gross_pnl == 2_000
        assert performance.net_pnl == 0
        assert performance.total_fee == 1_200
        assert performance.total_tax == 800
        assert performance.cost_to_gross_ratio == pytest.approx(1.0)

    def test_max_consecutive_losses(self):
        """
        목적: 최대 연속 손실은 청산일 순서로 센다.

        Given: 손실 2건이 연속하고 그 뒤에 이익이 오는 원장
        When: summarize 호출
        Then: 최대 연속 손실이 2다
        """
        # Given
        trades = [
            _trade(net_pnl=-100, exit_day=4),
            _trade(net_pnl=-200, exit_day=5),
            _trade(net_pnl=500, exit_day=6),
        ]
        result = _result(trades, [10_000_000, 10_000_100])

        # When
        performance = summarize(result, INITIAL_EQUITY)

        # Then
        assert performance.max_consecutive_losses == 2

    def test_empty_trades_do_not_raise(self):
        """
        목적: 트레이드가 0건이어도 요약이 나온다 — 신호가 없다는 것도 결과다.

        Given: 트레이드 없는 결과
        When: summarize 호출
        Then: 트레이드 수 0, 승률 0
        """
        # Given
        result = _result([], [10_000_000, 10_000_000])

        # When
        performance = summarize(result, INITIAL_EQUITY)

        # Then
        assert performance.trade_count == 0
        assert performance.win_rate_pct == 0.0

    def test_rejects_empty_equity(self):
        """
        목적: 자산 곡선이 없으면 성과를 만들어내지 않는다.

        Given: 빈 자산 곡선
        When: summarize 호출
        Then: ValueError
        """
        with pytest.raises(ValueError, match="자산 곡선"):
            summarize(BacktestResult(), INITIAL_EQUITY)


class TestFrames:
    """산출물 스키마를 고정한다."""

    def test_trades_frame_columns(self):
        """
        목적: 트레이드 원장 컬럼 순서를 계약으로 고정한다.

        Given: 트레이드 1건
        When: trades_frame 호출
        Then: 컬럼 순서가 계약과 같다
        """
        # Given / When
        frame = trades_frame(_result([_trade(net_pnl=100)], [10_000_000]))

        # Then
        assert list(frame.columns)[:5] == ["ticker", "market", "cluster_size", "base_bar_date", "decline_count"]
        assert "net_pnl" in frame.columns

    def test_equity_frame_columns(self):
        """
        목적: 자산 곡선 컬럼을 고정한다.

        Given: 이틀치 곡선
        When: equity_frame 호출
        Then: date·cash·equity 세 컬럼이다
        """
        # Given / When
        frame = equity_frame(_result([], [10_000_000, 10_100_000]))

        # Then
        assert list(frame.columns) == ["date", "cash", "equity"]
        assert len(frame) == 2
