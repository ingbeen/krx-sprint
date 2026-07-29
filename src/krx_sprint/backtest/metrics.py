"""성과 지표와 산출물

백테스트 설계 §9를 코드로 옮긴다.

이 모듈의 목적은 "얼마 벌었나"가 아니라 **그 숫자를 믿어도 되는지 판단할 재료**를 함께 내는 것이다.

- **비용 전/후 수익을 언제나 나란히** 낸다. 단타는 회전율이 높아 세금·수수료가 수익을 통째로 먹는다
- 미체결률·상한가로 놓친 주문·손절익절 동시 터치 건수 같은 **가정 사용량**을 함께 낸다.
  성과가 어떤 가정에 기대고 있는지는 이 숫자로만 보인다

반올림은 루트 CLAUDE.md 규칙을 따른다 — 백분율 2자리, 비율 4자리, 자본금은 정수.
"""

from dataclasses import dataclass
from typing import Any

import pandas as pd

from krx_sprint.backtest.engine import BacktestResult

# 연간 거래일 수 (변동성 연율화)
TRADING_DAYS_PER_YEAR = 252

# 연 환산에 쓰는 달력일 수
DAYS_PER_YEAR = 365.25

# 반올림 자릿수 (루트 CLAUDE.md 출력 데이터 반올림 규칙)
PERCENT_DECIMALS = 2
RATE_DECIMALS = 4
PRICE_DECIMALS = 0

# 산출물 컬럼 순서
TRADE_COLUMNS = [
    "ticker",
    "market",
    "cluster_size",
    "base_bar_date",
    "decline_count",
    "entry_date",
    "entry_price",
    "quantity",
    "exit_date",
    "exit_price",
    "exit_reason",
    "gross_pnl",
    "fee",
    "tax",
    "net_pnl",
    "holding_days",
    "invalidated",
]

EQUITY_COLUMNS = ["date", "cash", "equity"]


@dataclass(frozen=True)
class Performance:
    """성과 요약

    Attributes:
        initial_equity: 초기 자본 (원)
        final_equity: 종료 자본 (원)
        total_return_pct: 누적 수익률 (%)
        cagr_pct: 연평균 성장률 (%)
        max_drawdown_pct: 최대 낙폭 (%)
        volatility_pct: 연율화 변동성 (%)
        trade_count: 트레이드 수
        win_rate_pct: 승률 (%)
        payoff_ratio: 평균 이익 ÷ 평균 손실 (실현 손익비)
        expectancy: 트레이드당 평균 순손익 (원)
        avg_holding_days: 평균 보유일
        max_consecutive_losses: 최대 연속 손실 횟수
        exposure_pct: 포지션을 들고 있던 날의 비중 (%)
        gross_pnl: 비용 차감 전 손익 합계 (원)
        net_pnl: 비용 차감 후 손익 합계 (원)
        total_fee: 수수료 합계 (원)
        total_tax: 증권거래세 합계 (원)
        cost_to_gross_ratio: 총비용 ÷ |총손익| (비율)
    """

    initial_equity: int
    final_equity: int
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    volatility_pct: float
    trade_count: int
    win_rate_pct: float
    payoff_ratio: float
    expectancy: int
    avg_holding_days: float
    max_consecutive_losses: int
    exposure_pct: float
    gross_pnl: int
    net_pnl: int
    total_fee: int
    total_tax: int
    cost_to_gross_ratio: float


def trades_frame(result: BacktestResult) -> pd.DataFrame:
    """트레이드 원장을 DataFrame으로 만든다.

    Args:
        result: 백테스트 결과

    Returns:
        `TRADE_COLUMNS` 순서의 원장 (트레이드가 없으면 빈 프레임)
    """
    if not result.trades:
        return pd.DataFrame(columns=TRADE_COLUMNS)

    return pd.DataFrame([vars(trade) for trade in result.trades])[TRADE_COLUMNS]


def equity_frame(result: BacktestResult) -> pd.DataFrame:
    """자산 곡선을 DataFrame으로 만든다.

    Args:
        result: 백테스트 결과

    Returns:
        `EQUITY_COLUMNS` 순서의 자산 곡선
    """
    return pd.DataFrame(result.equity, columns=EQUITY_COLUMNS)


def summarize(result: BacktestResult, initial_equity: int) -> Performance:
    """성과 지표를 계산한다.

    Args:
        result: 백테스트 결과
        initial_equity: 초기 자본 (원)

    Returns:
        성과 요약

    Raises:
        ValueError: 자산 곡선이 비었거나 초기 자본이 0 이하인 경우
    """
    if initial_equity <= 0:
        raise ValueError(f"초기 자본은 0보다 커야 합니다: {initial_equity}")

    if not result.equity:
        raise ValueError("자산 곡선이 비어 있어 성과를 계산할 수 없습니다")

    equity = equity_frame(result)
    values = equity["equity"].astype(float)
    final_equity = int(values.iloc[-1])

    trades = trades_frame(result)
    wins = trades[trades["net_pnl"] > 0] if not trades.empty else trades
    losses = trades[trades["net_pnl"] <= 0] if not trades.empty else trades

    gross_pnl = int(trades["gross_pnl"].sum()) if not trades.empty else 0
    net_pnl = int(trades["net_pnl"].sum()) if not trades.empty else 0
    total_fee = int(trades["fee"].sum()) if not trades.empty else 0
    total_tax = int(trades["tax"].sum()) if not trades.empty else 0

    return Performance(
        initial_equity=initial_equity,
        final_equity=final_equity,
        total_return_pct=_percent(final_equity / initial_equity - 1),
        cagr_pct=_percent(_cagr(equity, initial_equity, final_equity)),
        max_drawdown_pct=_percent(_max_drawdown(values)),
        volatility_pct=_percent(_volatility(values)),
        trade_count=len(trades),
        win_rate_pct=_percent(len(wins) / len(trades)) if len(trades) else 0.0,
        payoff_ratio=_payoff_ratio(wins, losses),
        expectancy=int(net_pnl / len(trades)) if len(trades) else 0,
        avg_holding_days=round(float(trades["holding_days"].mean()), PERCENT_DECIMALS) if len(trades) else 0.0,
        max_consecutive_losses=_max_consecutive_losses(trades),
        exposure_pct=_percent(_exposure(equity)),
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        total_fee=total_fee,
        total_tax=total_tax,
        cost_to_gross_ratio=(round((total_fee + total_tax) / abs(gross_pnl), RATE_DECIMALS) if gross_pnl != 0 else 0.0),
    )


def summary_payload(
    performance: Performance,
    result: BacktestResult,
    params: dict[str, Any],
    execution: dict[str, Any],
) -> dict[str, Any]:
    """summary.json 에 담을 dict를 만든다.

    Args:
        performance: 성과 요약
        result: 백테스트 결과 (진단 집계를 꺼낸다)
        params: 전략 파라미터 dict
        execution: 체결 가정 dict

    Returns:
        저장용 dict
    """
    return {
        "performance": vars(performance),
        "diagnostics": result.diagnostics.as_dict(),
        "strategy_params": params,
        "execution_params": execution,
    }


def _percent(rate: float) -> float:
    """비율을 백분율로 바꿔 반올림한다.

    Args:
        rate: 비율

    Returns:
        백분율 (2자리)
    """
    return round(float(rate) * 100, PERCENT_DECIMALS)


def _cagr(equity: pd.DataFrame, initial_equity: int, final_equity: int) -> float:
    """연평균 성장률을 구한다.

    Args:
        equity: 자산 곡선
        initial_equity: 초기 자본 (원)
        final_equity: 종료 자본 (원)

    Returns:
        연평균 성장률 (비율)
    """
    span_days = (equity["date"].iloc[-1] - equity["date"].iloc[0]).days
    if span_days <= 0 or final_equity <= 0:
        return 0.0

    return (final_equity / initial_equity) ** (DAYS_PER_YEAR / span_days) - 1


def _max_drawdown(values: pd.Series) -> float:
    """최대 낙폭을 구한다.

    Args:
        values: 자산 시계열

    Returns:
        최대 낙폭 (비율, 음수)
    """
    peak = values.cummax()
    drawdown = values / peak - 1

    return float(drawdown.min())


def _volatility(values: pd.Series) -> float:
    """연율화 변동성을 구한다.

    Args:
        values: 자산 시계열

    Returns:
        연율화 변동성 (비율)
    """
    returns = values.pct_change().dropna()
    if len(returns) < 2:
        return 0.0

    return float(returns.std() * (TRADING_DAYS_PER_YEAR**0.5))


def _payoff_ratio(wins: pd.DataFrame, losses: pd.DataFrame) -> float:
    """실현 손익비를 구한다.

    Args:
        wins: 이익 트레이드
        losses: 손실 트레이드

    Returns:
        평균 이익 ÷ 평균 손실 (손실이 없으면 0)
    """
    if wins.empty or losses.empty:
        return 0.0

    average_loss = abs(float(losses["net_pnl"].mean()))
    if average_loss == 0:
        return 0.0

    return round(float(wins["net_pnl"].mean()) / average_loss, PERCENT_DECIMALS)


def _max_consecutive_losses(trades: pd.DataFrame) -> int:
    """최대 연속 손실 횟수를 구한다.

    Args:
        trades: 트레이드 원장

    Returns:
        최대 연속 손실 횟수
    """
    if trades.empty:
        return 0

    longest = 0
    current = 0
    for value in trades.sort_values("exit_date")["net_pnl"]:
        if value <= 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0

    return longest


def _exposure(equity: pd.DataFrame) -> float:
    """포지션을 들고 있던 날의 비중을 구한다.

    현금과 평가자산이 다르면 그날은 포지션을 들고 있었다는 뜻이다.

    Args:
        equity: 자산 곡선

    Returns:
        노출 비중 (비율)
    """
    if equity.empty:
        return 0.0

    return float((equity["cash"] != equity["equity"]).mean())
