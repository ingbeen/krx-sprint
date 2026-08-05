"""백테스트 엔진 — 일별 루프·포지션·자금 관리

백테스트 설계 §6·§7을 코드로 옮긴다.

하루의 처리 순서가 곧 **look-ahead 방지 장치**다.

1. 청산 판정 — 보유 포지션을 그날 봉으로 본다
2. 진입 체결 — **전날 만들어 둔 예약 주문**을 그날 봉으로 본다
3. 신호 산출 — 그날까지의 데이터로 다음 거래일 주문을 만든다

즉 주문 가격은 언제나 전일 종가까지의 정보로 정해지고, 체결은 다음 날 봉에서만 일어난다.
당일 종가로 당일 매매를 가정하지 않는다 (스펙 §10.1).

두 가격 축을 섞지 않는 것도 이 모듈의 책임이다. 지표는 **수정주가**로 계산하고,
주문 가격은 `k = 원본 종가 ÷ 수정 종가`로 **원본가 축**에 되돌려 낸다 (설계 §3.1).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from krx_sprint.backtest.costs import SETTLEMENT_LAG_DAYS, CostModel, OrderSide, align_price
from krx_sprint.backtest.execution import (
    DailyBar,
    ExitReason,
    fill_buy_limit,
    fill_buy_stop,
    is_tradable,
    max_quantity,
    resolve_exit,
)
from krx_sprint.backtest.params import EntryPriceKind, ExecutionParams, StopLossKind, StrategyParams
from krx_sprint.backtest.signals import (
    SwingKind,
    band_split_prices,
    count_declines,
    find_confirmed_swings,
    mark_base_bars,
    next_day_moving_average,
)
from krx_sprint.backtest.theme import find_clusters
from krx_sprint.common_constants import (
    COL_ADJ_CLOSE,
    COL_CLOSE,
    COL_DATE,
    COL_HIGH,
    COL_IS_HALTED,
    COL_IS_LAST_SEEN,
    COL_IS_LIMIT_DOWN_CLOSE,
    COL_IS_LIMIT_UP_CLOSE,
    COL_IS_SHARES_JUMP,
    COL_IS_UNADJUSTED_ACTION,
    COL_LOW,
    COL_MARKET,
    COL_OPEN,
    COL_TICKER,
    COL_VALUE,
)

# 밴드 하나당 매수 지정가 개수. 파라미터로 두지 않는 이유는 탐색 공간을 넓히지 않기 위해서다 —
# 흔들어 볼 필요가 생기면 그때 StrategyParams로 올린다
ORDERS_PER_BAND = 2


@dataclass(frozen=True)
class Order:
    """다음 거래일에 낼 매수 예약 주문

    한 주문이 지정가를 여러 개 담을 수 있다. 분할 매수는 "같은 종목을 여러 자리에서 나눠 산다"는
    뜻이므로 종목당 주문은 하나이고 그 안에 자리가 여러 개다.

    Attributes:
        ticker: 대상 티커
        market: 시장 구분
        cluster: 근거가 된 클러스터 구성 종목
        signal_date: 신호를 낸 일자 (= 주문 가격의 근거 시점)
        limit_prices: 매수 지정가 (원본가, 내림차순, 호가 정렬 완료)
        slice_count: 전략이 계획한 총 분할 수. 배분 금액을 나누는 분모다
        stop_price: 손절가 (원본가). 진입가 비례 방식이면 None
        base_bar_date: 근거 기준봉 일자
        decline_count: 진입 시점의 하락 차수
        is_refill: 보유 포지션에 더 담는 주문인지 여부. 그 포지션이 청산되면 함께 취소된다
        is_stop_entry: 역지정가 주문인지 여부. 참이면 가격이 **올라와야** 체결된다
    """

    ticker: str
    market: str
    cluster: tuple[str, ...]
    signal_date: date
    limit_prices: tuple[int, ...]
    slice_count: int
    stop_price: int | None
    base_bar_date: date
    decline_count: int
    is_refill: bool = False
    is_stop_entry: bool = False


@dataclass
class Position:
    """보유 중인 포지션

    분할 매수라 체결이 여러 번 일어난다. 체결 이력을 그대로 들고 있으면서 평균단가는 파생으로
    구한다 — 매입원가 합을 반올림된 평균단가로 되돌리면 손익에 오차가 생기기 때문이다.

    Attributes:
        slice_budget: 분할 한 자리에 배정된 금액 (원)
        remaining_slices: 아직 사지 못한 분할 수. 0이면 추가 매수를 걸지 않는다
        fills: 체결 이력 (체결가, 수량)
    """

    ticker: str
    market: str
    cluster: tuple[str, ...]
    entry_date: date
    stop_price: int
    target_price: int
    base_bar_date: date
    decline_count: int
    entry_fee: int
    slice_budget: int
    remaining_slices: int
    fills: list[tuple[int, int]] = field(default_factory=list)
    invalidated: bool = False

    @property
    def quantity(self) -> int:
        """보유 수량 (주)"""
        return sum(quantity for _, quantity in self.fills)

    @property
    def cost_basis(self) -> int:
        """매입원가 합 (원). 손익은 평균단가가 아니라 이 값으로 계산한다"""
        return sum(price * quantity for price, quantity in self.fills)

    @property
    def average_price(self) -> int:
        """평균단가 (원). 손절·익절 기준이며 원장에도 이 값이 남는다"""
        quantity = self.quantity
        if quantity <= 0:
            raise RuntimeError(f"내부 불변조건 위반: 체결이 없는 포지션의 평균단가 조회 (ticker={self.ticker})")

        return self.cost_basis // quantity


@dataclass
class Trade:
    """종료된 트레이드 한 건"""

    ticker: str
    market: str
    cluster_size: int
    base_bar_date: date
    decline_count: int
    entry_date: date
    avg_entry_price: int
    fill_count: int
    invested: int
    quantity: int
    exit_date: date
    exit_price: int
    exit_reason: str
    gross_pnl: int
    fee: int
    tax: int
    net_pnl: int
    holding_days: int
    invalidated: bool


@dataclass
class Diagnostics:
    """가정 의존도를 드러내는 진단 집계 (설계 §9.3)

    성과 숫자만 보면 "무엇을 가정했기에 이 결과가 나왔는지"를 알 수 없다.
    아래 항목이 그 가정의 사용량이다.
    """

    signal_count: int = 0
    order_count: int = 0
    unfilled_count: int = 0
    limit_up_blocked: int = 0
    halted_hold_days: int = 0
    stop_and_target_same_bar: int = 0
    invalidated_trades: int = 0
    delisting_exits: int = 0
    slot_blocked: int = 0
    cancelled_refills: int = 0

    def as_dict(self) -> dict[str, int]:
        """리포트용 dict로 변환한다."""
        return {
            "신호 수": self.signal_count,
            "주문 수": self.order_count,
            "미체결 수": self.unfilled_count,
            "상한가로 놓친 주문": self.limit_up_blocked,
            "거래정지 강제보유 일수": self.halted_hold_days,
            "손절·익절 동시 터치": self.stop_and_target_same_bar,
            "무효 처리 트레이드": self.invalidated_trades,
            "폐지 청산": self.delisting_exits,
            "슬롯 부족으로 포기": self.slot_blocked,
            "청산으로 취소된 추가매수": self.cancelled_refills,
        }


@dataclass
class BacktestResult:
    """백테스트 산출물

    Attributes:
        trades: 트레이드 원장
        equity: 일별 자산 곡선
        diagnostics: 진단 집계
    """

    trades: list[Trade] = field(default_factory=list)
    equity: list[tuple[date, int, int]] = field(default_factory=list)
    diagnostics: Diagnostics = field(default_factory=Diagnostics)


@dataclass
class _Theme:
    """급등일에 잡아 둔 테마

    되돌림이 나온 날은 정의상 급등일이 아니므로, 클러스터를 진입일에 다시 요구하면
    눌림목 진입 자체가 불가능해진다. 그래서 기준봉 유효기간 동안 테마를 들고 간다.

    Attributes:
        cluster: 클러스터 구성 종목
        leader: 대장주
        strength_score: 급등일에 매겨진 강도 점수. 여러 테마가 살아 있을 때 우선순위가 된다
        base_bar_position: 기준봉의 행 위치
        base_bar_date: 기준봉 일자
    """

    cluster: tuple[str, ...]
    leader: str
    strength_score: float
    base_bar_position: int
    base_bar_date: date


@dataclass
class _TickerHistory:
    """종목별로 미리 계산해 둔 시계열

    매일 종목 시계열을 다시 자르면 전 기간 실행이 끝나지 않는다. 기준봉 판정과
    수정주가 배열은 **인과적**(각 시점이 과거만 본다)이므로 한 번만 계산해 재사용해도
    look-ahead가 생기지 않는다.
    """

    positions: dict[date, int]
    adj_close: np.ndarray
    base_bar_positions: list[int]
    base_bar_dates: list[date]
    dates: list[date]


def run_backtest(
    panel: pd.DataFrame,
    trading_days: Sequence[date],
    params: StrategyParams,
    execution_params: ExecutionParams,
    *,
    start: date | None = None,
    end: date | None = None,
) -> BacktestResult:
    """패널 위에서 전략을 실행한다.

    Args:
        panel: 통합 패널 ((일자, 티커) 오름차순)
        trading_days: 거래일 캘린더 (비용 모델의 결제일 환산에 쓴다)
        params: 전략 파라미터
        execution_params: 체결·비용 가정
        start: 매매 시작 일자 (이전 구간은 지표 계산용으로만 쓴다)
        end: 매매 종료 일자

    Returns:
        트레이드 원장·자산 곡선·진단

    Raises:
        ValueError: 패널이 비었거나 구간이 뒤집힌 경우
    """
    if panel.empty:
        raise ValueError("패널이 비어 있습니다")

    if start is not None and end is not None and start > end:
        raise ValueError(f"구간이 뒤집혔습니다: {start.isoformat()} ~ {end.isoformat()}")

    # 매도 세율은 결제일(T+2 거래일) 기준이라 캘린더 끝 이틀은 세율을 확정할 수 없다.
    # 없는 거래일을 지어내는 대신 매매 구간을 그만큼 당긴다
    if len(trading_days) <= SETTLEMENT_LAG_DAYS:
        raise ValueError(f"거래일이 결제 지연({SETTLEMENT_LAG_DAYS}일)보다 적어 매매할 수 없습니다")

    tradable_end = trading_days[-(SETTLEMENT_LAG_DAYS + 1)]
    end = tradable_end if end is None else min(end, tradable_end)

    cost_model = CostModel(trading_days, execution_params.fee_rate, include_tax=execution_params.include_tax)
    histories = _build_histories(panel, params)
    day_slices = _build_day_slices(panel)
    all_dates = sorted(day_slices)

    result = BacktestResult()
    cash = params.initial_equity
    positions: dict[str, Position] = {}
    pending: list[Order] = []
    listed_days: dict[str, int] = {}
    themes: dict[str, _Theme] = {}
    last_date = min(all_dates[-1], end)

    for target in all_dates:
        rows = day_slices[target]
        bars = _build_bars(rows)
        for ticker in bars:
            listed_days[ticker] = listed_days.get(ticker, 0) + 1

        tradable_window = start is None or target >= start
        if target > end:
            break

        # 1. 청산 → 2. 진입 → 3. 신호 순서가 look-ahead 방지의 핵심이다
        if tradable_window:
            cash = _close_positions(positions, bars, target, cost_model, execution_params, result, cash, last_date)
            cash = _open_positions(pending, positions, bars, target, cost_model, execution_params, result, cash, params)

        pending = []
        if tradable_window:
            pending = _build_orders(
                panel, histories, bars, target, params, positions, listed_days, result, cash, themes
            )

        equity = cash + sum(
            position.quantity * int(bars[position.ticker].close)
            for position in positions.values()
            if position.ticker in bars and bars[position.ticker].close > 0
        )
        if tradable_window:
            result.equity.append((target, int(cash), int(equity)))

    return result


def _build_day_slices(panel: pd.DataFrame) -> dict[date, pd.DataFrame]:
    """일자별 행 묶음을 만든다.

    패널이 (일자, 티커) 오름차순이라 일자별 행은 **연속 구간**이다. 경계만 찾아 잘라내면
    groupby보다 빠르고 일자 타입도 명확하게 유지된다.

    Args:
        panel: 통합 패널 (일자 오름차순)

    Returns:
        일자 → 그날의 행

    Raises:
        ValueError: 패널이 일자 오름차순이 아닌 경우
    """
    if not panel[COL_DATE].is_monotonic_increasing:
        raise ValueError("패널이 일자 오름차순이 아닙니다")

    values = panel[COL_DATE].to_numpy()
    unique = pd.DatetimeIndex(panel[COL_DATE].unique())
    starts = np.searchsorted(values, unique.to_numpy(), side="left")
    ends = np.searchsorted(values, unique.to_numpy(), side="right")

    return {
        stamp.date(): panel.iloc[int(start) : int(end)] for stamp, start, end in zip(unique, starts, ends, strict=True)
    }


def _build_bars(rows: pd.DataFrame) -> dict[str, DailyBar]:
    """하루치 행을 체결 판정용 일봉으로 바꾼다.

    행 단위 순회(`iterrows`)는 전 기간 470만 행에서 감당이 안 되므로 컬럼 배열을 zip 한다.

    Args:
        rows: 한 일자의 패널 행

    Returns:
        티커 → 일봉
    """
    return {
        str(ticker): DailyBar(
            open=int(open_price),
            high=int(high),
            low=int(low),
            close=int(close),
            value=int(value),
            market=str(market),
            is_halted=bool(halted),
            is_limit_up_close=bool(limit_up),
            is_limit_down_close=bool(limit_down),
            is_shares_jump=bool(shares_jump),
            is_last_seen=bool(last_seen),
            is_unadjusted_action=bool(unadjusted),
        )
        for ticker, open_price, high, low, close, value, market, halted, limit_up, limit_down, shares_jump, last_seen, unadjusted in zip(
            rows[COL_TICKER].to_numpy(),
            rows[COL_OPEN].to_numpy(),
            rows[COL_HIGH].to_numpy(),
            rows[COL_LOW].to_numpy(),
            rows[COL_CLOSE].to_numpy(),
            rows[COL_VALUE].to_numpy(),
            rows[COL_MARKET].to_numpy(),
            rows[COL_IS_HALTED].to_numpy(),
            rows[COL_IS_LIMIT_UP_CLOSE].to_numpy(),
            rows[COL_IS_LIMIT_DOWN_CLOSE].to_numpy(),
            rows[COL_IS_SHARES_JUMP].to_numpy(),
            rows[COL_IS_LAST_SEEN].to_numpy(),
            rows[COL_IS_UNADJUSTED_ACTION].to_numpy(),
            strict=True,
        )
    }


def _build_histories(panel: pd.DataFrame, params: StrategyParams) -> dict[str, _TickerHistory]:
    """종목별 시계열과 기준봉 위치를 미리 계산한다.

    Args:
        panel: 통합 패널
        params: 전략 파라미터

    Returns:
        티커 → 시계열
    """
    histories: dict[str, _TickerHistory] = {}

    for ticker, group in panel.groupby(COL_TICKER, observed=True):
        frame = group.reset_index(drop=True)
        dates = [value.date() for value in frame[COL_DATE]]
        marked = mark_base_bars(frame, params)
        base_positions = [position for position, flag in enumerate(marked) if bool(flag)]

        histories[str(ticker)] = _TickerHistory(
            positions={value: index for index, value in enumerate(dates)},
            adj_close=frame[COL_ADJ_CLOSE].to_numpy(dtype=float),
            base_bar_positions=base_positions,
            base_bar_dates=[dates[position] for position in base_positions],
            dates=dates,
        )

    return histories


def _close_positions(
    positions: dict[str, Position],
    bars: dict[str, DailyBar],
    target: date,
    cost_model: CostModel,
    execution_params: ExecutionParams,
    result: BacktestResult,
    cash: int,
    last_date: date,
) -> int:
    """보유 포지션의 청산을 판정한다.

    Args:
        positions: 보유 포지션 (청산 시 제거된다)
        bars: 그날의 일봉
        target: 대상 일자
        cost_model: 비용 계산기
        execution_params: 체결 가정
        result: 결과 누적 대상
        cash: 현재 현금
        last_date: 백테스트 구간의 마지막 거래일 (폐지와 기간 종료를 구분한다)

    Returns:
        갱신된 현금
    """
    for ticker in list(positions):
        position = positions[ticker]
        bar = bars.get(ticker)

        # 패널에서 사라졌다 = 폐지. 마지막으로 본 종가로 청산한다
        if bar is None:
            cash = _record_exit(
                position, position.average_price, target, ExitReason.DELISTING, cost_model, result, cash
            )
            del positions[ticker]
            result.diagnostics.delisting_exits += 1
            continue

        if bar.is_halted:
            result.diagnostics.halted_hold_days += 1

        # 수정 미반영 구간을 지난 트레이드는 수익률을 신뢰할 수 없다 — 숨기지 않고 표시만 한다
        if bar.is_unadjusted_action:
            position.invalidated = True

        if bar.low <= position.stop_price and bar.high >= position.target_price and is_tradable(bar):
            result.diagnostics.stop_and_target_same_bar += 1

        exit_fill = resolve_exit(
            bar,
            stop_price=position.stop_price,
            target_price=position.target_price,
            trade_date=target,
            params=execution_params,
        )

        if exit_fill is not None:
            cash = _record_exit(position, exit_fill.price, target, exit_fill.reason, cost_model, result, cash)
            del positions[ticker]
            continue

        # 1단 최종 등장일이면 다음 거래일이 없다 — 종가로 정리한다.
        # 데이터 마지막 날이면 폐지가 아니라 백테스트 종료이므로 사유를 구분한다
        if bar.is_last_seen:
            is_delisting = target < last_date
            price = _delisting_price(bar.close, execution_params) if is_delisting else max(1, bar.close)
            reason = ExitReason.DELISTING if is_delisting else ExitReason.EXPIRY
            cash = _record_exit(position, price, target, reason, cost_model, result, cash)
            del positions[ticker]
            if is_delisting:
                result.diagnostics.delisting_exits += 1

    return cash


def _delisting_price(close: int, execution_params: ExecutionParams) -> int:
    """폐지 청산가를 구한다.

    실제 정리매매는 훨씬 불리하므로 페널티를 시나리오로 둔다 (설계 §7.2).

    Args:
        close: 마지막 종가 (원)
        execution_params: 체결 가정

    Returns:
        청산가 (원, 최소 1)
    """
    return max(1, int(close * (1 - execution_params.delisting_penalty_rate)))


def _record_exit(
    position: Position,
    exit_price: int,
    target: date,
    reason: ExitReason,
    cost_model: CostModel,
    result: BacktestResult,
    cash: int,
) -> int:
    """청산을 원장에 기록하고 현금을 갱신한다.

    Args:
        position: 청산할 포지션
        exit_price: 체결가 (원)
        target: 청산 일자
        reason: 청산 사유
        cost_model: 비용 계산기
        result: 결과 누적 대상
        cash: 현재 현금

    Returns:
        갱신된 현금
    """
    quantity = position.quantity
    price = max(1, exit_price)
    cost = cost_model.sell_cost(price, quantity, target, position.market)
    proceeds = price * quantity - cost.total
    # 손익은 반올림된 평균단가가 아니라 매입원가 합으로 낸다 (분할 체결에서 오차가 생기지 않게)
    gross = price * quantity - position.cost_basis

    result.trades.append(
        Trade(
            ticker=position.ticker,
            market=position.market,
            cluster_size=len(position.cluster),
            base_bar_date=position.base_bar_date,
            decline_count=position.decline_count,
            entry_date=position.entry_date,
            avg_entry_price=position.average_price,
            fill_count=len(position.fills),
            invested=position.cost_basis,
            quantity=quantity,
            exit_date=target,
            exit_price=price,
            exit_reason=reason.value,
            gross_pnl=gross,
            fee=position.entry_fee + cost.fee,
            tax=cost.tax,
            net_pnl=gross - position.entry_fee - cost.total,
            holding_days=(target - position.entry_date).days,
            invalidated=position.invalidated,
        )
    )

    if position.invalidated:
        result.diagnostics.invalidated_trades += 1

    return cash + proceeds


def _open_positions(
    pending: list[Order],
    positions: dict[str, Position],
    bars: dict[str, DailyBar],
    target: date,
    cost_model: CostModel,
    execution_params: ExecutionParams,
    result: BacktestResult,
    cash: int,
    params: StrategyParams,
) -> int:
    """전날 만든 예약 주문의 체결을 판정한다.

    주문 하나에 지정가가 여러 개 있을 수 있고(분할 매수), 이미 보유 중인 종목이면 **기존 포지션에
    누적**한다. 체결이 하나라도 붙으면 평균단가가 바뀌므로 손절가·익절가를 그 자리에서 다시 잡는다.

    Args:
        pending: 전날 만든 주문
        positions: 보유 포지션 (체결 시 추가·갱신된다)
        bars: 그날의 일봉
        target: 대상 일자
        cost_model: 비용 계산기
        execution_params: 체결 가정
        result: 결과 누적 대상
        cash: 현재 현금
        params: 전략 파라미터

    Returns:
        갱신된 현금
    """
    for order in pending:
        position = positions.get(order.ticker)

        # 추가 매수는 그 포지션이 살아 있을 때만 유효하다. 청산된 뒤에 체결되면 이미 끝난
        # 트레이드의 손절가·기준봉을 물려받은 새 포지션이 열린다
        if order.is_refill and position is None:
            result.diagnostics.cancelled_refills += 1
            continue

        if position is None and len(positions) >= params.max_positions:
            result.diagnostics.slot_blocked += 1
            continue

        bar = bars.get(order.ticker)
        if bar is None:
            result.diagnostics.unfilled_count += 1
            continue

        if bar.is_limit_up_close:
            result.diagnostics.limit_up_blocked += 1

        slice_budget = (
            position.slice_budget
            if position is not None
            else min(cash, cash // max(1, params.max_positions - len(positions))) // order.slice_count
        )

        fills, cash = _fill_slices(order, bar, slice_budget, cash, cost_model, execution_params, result)
        if not fills:
            continue

        position = _apply_fills(order, position, fills, target, slice_budget, params, bar)
        positions[order.ticker] = position

    return cash


def _fill_slices(
    order: Order,
    bar: DailyBar,
    slice_budget: int,
    cash: int,
    cost_model: CostModel,
    execution_params: ExecutionParams,
    result: BacktestResult,
) -> tuple[list[tuple[int, int, int]], int]:
    """주문의 지정가를 위에서부터 훑어 체결분을 모은다.

    유동성 상한은 **하루 단위**라 자리마다 새로 주어지지 않는다. 자리별로 상한을 다시 주면
    분할 수만큼 상한이 늘어나 "거래대금의 1%"라는 가정이 무너진다.

    Args:
        order: 대상 주문
        bar: 그날의 일봉
        slice_budget: 분할 한 자리에 배정된 금액 (원)
        cash: 현재 현금
        cost_model: 비용 계산기
        execution_params: 체결 가정
        result: 결과 누적 대상

    Returns:
        (체결가, 수량, 수수료) 목록과 갱신된 현금
    """
    fills: list[tuple[int, int, int]] = []
    liquidity_left = int(bar.value * execution_params.liquidity_participation_rate)
    filled_any = False

    for limit_price in order.limit_prices:
        fill_price = fill_buy_stop(bar, limit_price) if order.is_stop_entry else fill_buy_limit(bar, limit_price)
        if fill_price is None:
            break

        budget = min(slice_budget, cash, liquidity_left)
        quantity = max_quantity(bar, fill_price, budget, execution_params) if budget > 0 else 0
        if quantity <= 0:
            break

        cost = cost_model.buy_cost(fill_price, quantity)
        spent = fill_price * quantity
        cash -= spent + cost.total
        liquidity_left -= spent
        fills.append((fill_price, quantity, cost.fee))
        filled_any = True

    if not filled_any:
        result.diagnostics.unfilled_count += 1

    return fills, cash


def _apply_fills(
    order: Order,
    position: Position | None,
    fills: list[tuple[int, int, int]],
    target: date,
    slice_budget: int,
    params: StrategyParams,
    bar: DailyBar,
) -> Position:
    """체결분을 포지션에 반영하고 손절·익절을 평균단가 기준으로 다시 잡는다.

    Args:
        order: 대상 주문
        position: 기존 포지션 (신규 진입이면 None)
        fills: (체결가, 수량, 수수료) 목록
        target: 체결일
        slice_budget: 분할 한 자리에 배정된 금액 (원)
        params: 전략 파라미터
        bar: 그날의 일봉 (호가 정렬에 시장 구분이 필요하다)

    Returns:
        갱신된 포지션
    """
    if position is None:
        position = Position(
            ticker=order.ticker,
            market=order.market,
            cluster=order.cluster,
            entry_date=target,
            stop_price=0,
            target_price=0,
            base_bar_date=order.base_bar_date,
            decline_count=order.decline_count,
            entry_fee=0,
            slice_budget=slice_budget,
            remaining_slices=order.slice_count,
        )

    for fill_price, quantity, fee in fills:
        position.fills.append((fill_price, quantity))
        position.entry_fee += fee

    position.remaining_slices = max(0, position.remaining_slices - len(fills))

    average = position.average_price
    stop_price = order.stop_price if order.stop_price is not None else int(average * (1 - params.stop_loss_rate))
    position.stop_price = min(stop_price, average - 1)
    position.target_price = align_price(
        average + (average - position.stop_price) * params.reward_risk_ratio,
        bar.market,
        target,
        side=OrderSide.SELL,
    )

    return position


def _build_orders(
    panel: pd.DataFrame,
    histories: dict[str, _TickerHistory],
    bars: dict[str, DailyBar],
    target: date,
    params: StrategyParams,
    positions: dict[str, Position],
    listed_days: dict[str, int],
    result: BacktestResult,
    cash: int,
    themes: dict[str, _Theme],
) -> list[Order]:
    """그날까지의 데이터로 다음 거래일 주문을 만든다.

    **테마는 급등일에 서고 진입은 눌림일에 한다.** 되돌림이 나온 날은 정의상 급등일이 아니므로,
    클러스터를 그날 다시 요구하면 영원히 진입할 수 없다. 그래서 급등일에 잡은 테마를 기준봉
    유효기간 동안 들고 있다가, 하락 차수가 진입 구간에 들어오는 날 주문을 만든다.

    **주문 수는 남은 슬롯 수를 넘지 않는다.** 슬롯보다 많이 만들어 두고 앞에서부터 채우면
    무엇을 버릴지가 순회 순서로 정해진다 — 선택 기준 없이 후보를 버리는 셈이다. 강한 테마부터
    평가해 슬롯이 차면 멈추는 방식이라야 "가장 강한 테마를 산다"가 성립한다.

    보유 중인 종목에는 **남은 분할의 추가 매수 주문**을 다시 건다. 이동평균은 매일 움직이므로
    어제 걸어 둔 가격을 그대로 쓸 수 없다.

    Args:
        panel: 통합 패널
        histories: 종목별 사전 계산 시계열
        bars: 그날의 일봉
        target: 신호일
        params: 전략 파라미터
        positions: 보유 포지션 (중복 진입 차단과 추가 매수에 쓴다)
        listed_days: 티커별 상장 경과 거래일
        result: 결과 누적 대상
        cash: 현재 현금
        themes: 살아 있는 테마 (대장주 → 테마). 이 함수가 갱신한다

    Returns:
        다음 거래일 주문 목록
    """
    _refresh_themes(panel, histories, target, params, listed_days, result, themes)

    if cash <= 0:
        return []

    orders = _build_refill_orders(histories, bars, positions, target, params)

    held_clusters = {position.cluster for position in positions.values()}
    available_slots = params.max_positions - len(positions)
    new_orders = 0

    # 강도 내림차순 — 동점이면 대장주 티커로 순서를 고정해 실행이 재현되게 한다
    ranked = sorted(themes.values(), key=lambda theme: (-theme.strength_score, theme.leader))

    for theme in ranked:
        if new_orders >= available_slots:
            break

        if theme.cluster in held_clusters or theme.leader in positions:
            continue

        order = _build_order(histories, bars, theme, target, params)
        if order is not None:
            orders.append(order)
            new_orders += 1

    result.diagnostics.order_count += len(orders)
    return orders


def _build_refill_orders(
    histories: dict[str, _TickerHistory],
    bars: dict[str, DailyBar],
    positions: dict[str, Position],
    target: date,
    params: StrategyParams,
) -> list[Order]:
    """보유 종목의 남은 분할을 다시 주문한다.

    남은 자리는 **가격이 낮은 쪽부터** 채운다. 위쪽 자리는 이미 지나갔으므로 추가 매수는 언제나
    더 아래에서 일어나야 한다.

    Args:
        histories: 종목별 사전 계산 시계열
        bars: 그날의 일봉
        positions: 보유 포지션
        target: 신호일
        params: 전략 파라미터

    Returns:
        추가 매수 주문 목록
    """
    orders: list[Order] = []

    for position in positions.values():
        if position.remaining_slices <= 0:
            continue

        history = histories.get(position.ticker)
        bar = bars.get(position.ticker)
        if history is None or bar is None or not is_tradable(bar):
            continue

        index = history.positions.get(target)
        if index is None:
            continue

        prices = _entry_limit_prices(history, index, bar, target, params)
        if prices is None:
            continue

        stop_price = _stop_price(history, index, bar, target, params, prices[-1])
        if not _is_stop_price_usable(stop_price, prices[-1], params):
            continue

        orders.append(
            Order(
                ticker=position.ticker,
                market=bar.market,
                cluster=position.cluster,
                signal_date=target,
                limit_prices=prices[-position.remaining_slices :],
                slice_count=position.remaining_slices,
                stop_price=stop_price,
                base_bar_date=position.base_bar_date,
                decline_count=position.decline_count,
                is_refill=True,
                is_stop_entry=params.entry_price_kind is EntryPriceKind.MA_RECLAIM,
            )
        )

    return orders


def _is_stop_price_usable(stop_price: int | None, limit_price: int, params: StrategyParams) -> bool:
    """손절가를 쓸 수 있는지 본다.

    고정 비율 방식만 None이 정상이다 — 실제 평균단가가 정해진 뒤에 계산하기 때문이다.
    지표 기반 방식에서 None이면 손절선을 모른다는 뜻이므로, 조용히 다른 방식으로 넘어가지 않고
    주문을 만들지 않는다.

    Args:
        stop_price: 계산된 손절가 (원)
        limit_price: 가장 낮은 매수 지정가 (원)
        params: 전략 파라미터

    Returns:
        주문을 만들어도 되는지 여부
    """
    if params.stop_loss_kind is StopLossKind.FIXED:
        return True

    return stop_price is not None and stop_price < limit_price


def _refresh_themes(
    panel: pd.DataFrame,
    histories: dict[str, _TickerHistory],
    target: date,
    params: StrategyParams,
    listed_days: dict[str, int],
    result: BacktestResult,
    themes: dict[str, _Theme],
) -> None:
    """당일 클러스터를 반영하고 만료된 테마를 정리한다.

    Args:
        panel: 통합 패널
        histories: 종목별 사전 계산 시계열
        target: 신호일
        params: 전략 파라미터
        listed_days: 티커별 상장 경과 거래일
        result: 결과 누적 대상
        themes: 살아 있는 테마 (제자리에서 갱신된다)
    """
    # 1. 유효기간이 지난 테마를 버린다
    for leader in list(themes):
        history = histories.get(leader)
        position = None if history is None else history.positions.get(target)
        if history is None or position is None:
            continue
        if position - themes[leader].base_bar_position > params.base_bar_expiry_days:
            del themes[leader]

    # 2. 당일 클러스터를 등록한다 (기준봉이 살아 있는 대장주만)
    window = _window_slice(panel, target, params.correlation_window)
    clusters = find_clusters(window, target, params, listed_days=listed_days)
    if not clusters:
        return

    result.diagnostics.signal_count += len(clusters)

    for cluster in clusters:
        history = histories.get(cluster.leader)
        if history is None:
            continue

        position = history.positions.get(target)
        if position is None:
            continue

        base_position = _latest_base_bar(history, position, params)
        if base_position is None:
            continue

        themes[cluster.leader] = _Theme(
            cluster=cluster.tickers,
            leader=cluster.leader,
            strength_score=cluster.strength_score,
            base_bar_position=base_position,
            base_bar_date=history.dates[base_position],
        )


def _window_slice(panel: pd.DataFrame, target: date, window: int) -> pd.DataFrame:
    """상관 창 계산에 필요한 만큼만 잘라낸다.

    전 이력을 매일 넘기면 필터 비용이 일자 수에 비례해 커진다.

    Args:
        panel: 통합 패널
        target: 신호일
        window: 상관 창 (거래일)

    Returns:
        신호일까지의 최근 구간
    """
    # 휴장을 감안해 거래일 수의 세 배를 달력일로 잡는다
    last = pd.Timestamp(target)
    first = pd.Timestamp(target - timedelta(days=window * 3))

    return panel[(panel[COL_DATE] >= first) & (panel[COL_DATE] <= last)]


def _build_order(
    histories: dict[str, _TickerHistory],
    bars: dict[str, DailyBar],
    theme: _Theme,
    target: date,
    params: StrategyParams,
) -> Order | None:
    """대장주 한 종목의 진입 주문을 만든다.

    하락 차수가 허용 범위(1 ~ `max_decline_count`)일 때만 주문한다.
    **3차 하락 이상은 진입 금지**가 하드룰이다.

    Args:
        histories: 종목별 사전 계산 시계열
        bars: 그날의 일봉
        theme: 진입 근거가 되는 테마
        target: 신호일
        params: 전략 파라미터

    Returns:
        주문 (조건 미충족이면 None)
    """
    history = histories.get(theme.leader)
    bar = bars.get(theme.leader)
    if history is None or bar is None or not is_tradable(bar):
        return None

    position = history.positions.get(target)
    if position is None or position < theme.base_bar_position:
        return None

    prices = history.adj_close[: position + 1].tolist()
    if len(prices) < 2 or prices[position] <= 0:
        return None

    counts = count_declines(prices, theme.base_bar_position, params)
    decline_count = counts[position]
    if not 1 <= decline_count <= params.max_decline_count:
        return None

    limit_prices = _entry_limit_prices(history, position, bar, target, params)
    if limit_prices is None:
        return None

    stop_price = _stop_price(history, position, bar, target, params, limit_prices[-1])
    if not _is_stop_price_usable(stop_price, limit_prices[-1], params):
        return None

    return Order(
        ticker=theme.leader,
        market=bar.market,
        cluster=theme.cluster,
        signal_date=target,
        limit_prices=limit_prices,
        slice_count=len(limit_prices),
        stop_price=stop_price,
        base_bar_date=theme.base_bar_date,
        decline_count=decline_count,
        is_stop_entry=params.entry_price_kind is EntryPriceKind.MA_RECLAIM,
    )


def _latest_base_bar(history: _TickerHistory, position: int, params: StrategyParams) -> int | None:
    """유효기간 안에 있는 가장 최근 기준봉 위치를 찾는다.

    Args:
        history: 종목 시계열
        position: 신호일의 행 위치
        params: 전략 파라미터

    Returns:
        기준봉 위치 (없으면 None)
    """
    for base_position in reversed(history.base_bar_positions):
        if base_position > position:
            continue
        if position - base_position > params.base_bar_expiry_days:
            return None
        return base_position

    return None


def _axis_factor(history: _TickerHistory, position: int, bar: DailyBar) -> float | None:
    """수정주가 축 값을 원본가 축으로 되돌릴 계수를 구한다 (설계 §3.1).

    `k = 원본 종가 ÷ 수정 종가`이며 신호일 값만 쓴다 — 그래서 PIT가 깨지지 않는다.

    Args:
        history: 종목 시계열
        position: 신호일의 행 위치
        bar: 신호일 일봉

    Returns:
        변환 계수 (구할 수 없으면 None)
    """
    adjusted = float(history.adj_close[position])
    if adjusted <= 0 or bar.close <= 0:
        return None

    return bar.close / adjusted


def _entry_limit_prices(
    history: _TickerHistory,
    position: int,
    bar: DailyBar,
    target: date,
    params: StrategyParams,
) -> tuple[int, ...] | None:
    """매수 지정가를 구한다 (설계 §6.1).

    밴드 분할 방식만 여러 개를 돌려주고 나머지는 하나다.

    수정주가로 계산한 지표는 축 변환 계수로 원본가에 되돌린 뒤 호가에 맞춘다.
    매수는 **내림** 정렬이라 체결 조건이 더 빡빡해진다.

    Args:
        history: 종목 시계열
        position: 신호일의 행 위치
        bar: 신호일 일봉
        target: 신호일
        params: 전략 파라미터

    Returns:
        지정가 (내림차순). 구할 수 없으면 None
    """
    if params.entry_price_kind is EntryPriceKind.MA_BAND_SPLIT:
        return _band_limit_prices(history, position, bar, target, params)

    if params.entry_price_kind is EntryPriceKind.MA_RECLAIM:
        return _reclaim_limit_prices(history, position, bar, target, params)

    if params.entry_price_kind is EntryPriceKind.CLOSE_DISCOUNT:
        raw_price = bar.close * (1 - params.entry_discount_rate)
    elif params.entry_price_kind is EntryPriceKind.PREVIOUS_LOW:
        raw_price = float(bar.low)
    else:
        average = _moving_average(history, position, params.entry_ma_period)
        factor = _axis_factor(history, position, bar)
        if average is None or factor is None:
            return None
        raw_price = average * factor

    if raw_price <= 0:
        return None

    aligned = align_price(raw_price, bar.market, target, side=OrderSide.BUY)
    return (aligned,) if aligned > 0 else None


def _reclaim_limit_prices(
    history: _TickerHistory,
    position: int,
    bar: DailyBar,
    target: date,
    params: StrategyParams,
) -> tuple[int, ...] | None:
    """이탈한 이동평균선을 되찾는 자리의 역지정가를 구한다.

    **깨진 선을 되찾는 것을 확인하고 산다.** 신호일 종가가 어떤 선 아래로 마감했으면 그 선을
    이탈한 것이고, 그 중 **가장 낮은 선**(= 종가 바로 위)이 되찾아야 할 자리다. 아직 깬 선이
    없으면 살 이유가 없다.

    분할하지 않는다 — 되찾음을 확인한 시점이 하나뿐이라 나눌 자리가 없다.

    Args:
        history: 종목 시계열
        position: 신호일의 행 위치
        bar: 신호일 일봉
        target: 신호일
        params: 전략 파라미터

    Returns:
        역지정가 한 개. 역배열이거나 이탈한 선이 없으면 None
    """
    bounds = _band_bounds(history, position, bar, params)
    if bounds is None:
        return None

    broken = [price for price in bounds if price > bar.close]
    if not broken:
        return None

    # 매수는 내림 정렬이지만 역지정가는 그만큼 더 빨리 체결되므로 올림으로 불리하게 맞춘다
    aligned = align_price(min(broken), bar.market, target, side=OrderSide.SELL)

    return (aligned,) if aligned > 0 else None


def _band_bounds(
    history: _TickerHistory,
    position: int,
    bar: DailyBar,
    params: StrategyParams,
) -> list[float] | None:
    """밴드 경계가 되는 이동평균을 원본가 축으로 구한다.

    정배열이 아니면 밴드 정의가 성립하지 않으므로 값을 주지 않는다.

    Args:
        history: 종목 시계열
        position: 신호일의 행 위치
        bar: 신호일 일봉
        params: 전략 파라미터

    Returns:
        경계 가격 (짧은 이동평균부터). 구할 수 없거나 역배열이면 None
    """
    factor = _axis_factor(history, position, bar)
    if factor is None:
        return None

    prices = history.adj_close.tolist()
    bounds: list[float] = []

    for period in params.entry_band_periods:
        average = next_day_moving_average(prices, position, period)
        if average is None:
            return None
        bounds.append(average * factor)

    if any(bounds[index] <= bounds[index + 1] for index in range(len(bounds) - 1)):
        return None

    return bounds


def _band_limit_prices(
    history: _TickerHistory,
    position: int,
    bar: DailyBar,
    target: date,
    params: StrategyParams,
) -> tuple[int, ...] | None:
    """이동평균 밴드를 나눈 분할 매수 지정가를 구한다.

    **이미 신호일 종가 아래로 내려간 자리는 제외한다.** 그 자리는 지나간 가격이라 다음 날 시가에
    통째로 체결돼 "분할해서 나눠 산다"가 성립하지 않는다.

    Args:
        history: 종목 시계열
        position: 신호일의 행 위치
        bar: 신호일 일봉
        target: 신호일
        params: 전략 파라미터

    Returns:
        지정가 (내림차순). 역배열이거나 살 자리가 없으면 None
    """
    bounds = _band_bounds(history, position, bar, params)
    if bounds is None:
        return None

    aligned = [
        align_price(price, bar.market, target, side=OrderSide.BUY)
        for price in band_split_prices(bounds, ORDERS_PER_BAND)
    ]
    usable = tuple(price for price in aligned if 0 < price < bar.close)

    return usable if usable else None


def _stop_price(
    history: _TickerHistory,
    position: int,
    bar: DailyBar,
    target: date,
    params: StrategyParams,
    limit_price: int,
) -> int | None:
    """손절가를 구한다 (설계 §6.2).

    고정 비율 방식은 **실제 진입가가 정해진 뒤** 계산해야 하므로 None을 돌려주고,
    체결 시점에 엔진이 평균단가 기준으로 잡는다.

    밴드 하한선 방식은 가장 긴 이동평균 아래에 여유를 둔다. 그 선이 매수 하한이므로 아래로
    이탈하면 눌림목 가설이 무너진 것이고, 여유가 없으면 마지막 분할 자리와 붙어 사자마자 잘린다.

    Args:
        history: 종목 시계열
        position: 신호일의 행 위치
        bar: 신호일 일봉
        target: 신호일
        params: 전략 파라미터
        limit_price: 가장 낮은 매수 지정가 (원)

    Returns:
        손절가 (진입가 기준으로 미루면 None)
    """
    if params.stop_loss_kind is StopLossKind.FIXED:
        return None

    factor = _axis_factor(history, position, bar)
    if factor is None:
        return None

    if params.stop_loss_kind is StopLossKind.BAND_FLOOR:
        floor = next_day_moving_average(history.adj_close.tolist(), position, params.entry_band_periods[-1])
        reference = None if floor is None else floor * factor * (1 - params.stop_loss_rate)
    elif params.stop_loss_kind is StopLossKind.MOVING_AVERAGE:
        average = _moving_average(history, position, params.stop_loss_ma_period)
        reference = None if average is None else average * factor
    else:
        swing_low = _last_swing_low(history, position, params)
        reference = None if swing_low is None else swing_low * factor * (1 - params.stop_loss_rate)

    if reference is None or reference <= 0 or reference >= limit_price:
        return None

    return align_price(reference, bar.market, target, side=OrderSide.BUY)


def _moving_average(history: _TickerHistory, position: int, window: int) -> float | None:
    """수정주가 이동평균을 구한다.

    Args:
        history: 종목 시계열
        position: 기준 행 위치
        window: 창 길이 (거래일)

    Returns:
        이동평균 (창을 못 채우면 None)
    """
    if position + 1 < window:
        return None

    values = history.adj_close[position + 1 - window : position + 1]
    if (values <= 0).any():
        return None

    return float(values.mean())


def _last_swing_low(history: _TickerHistory, position: int, params: StrategyParams) -> float | None:
    """신호일까지 확정된 마지막 스윙 저점을 찾는다.

    확정 위치가 신호일 이후인 스윙은 아직 알 수 없으므로 쓰지 않는다 (look-ahead 방지).

    Args:
        history: 종목 시계열
        position: 신호일의 행 위치
        params: 전략 파라미터

    Returns:
        스윙 저점 가격 (없으면 None)
    """
    prices = history.adj_close[: position + 1].tolist()
    if len(prices) < 2:
        return None

    lows = [
        swing.price
        for swing in find_confirmed_swings(prices, params.swing_reversal_rate)
        if swing.kind is SwingKind.LOW and swing.confirmed_position <= position
    ]

    return lows[-1] if lows else None
