"""체결 가정

일봉 + 예약매매 전제에서 주문이 체결됐는지를 판정한다 (백테스트 설계 §7).

일봉만으로는 장중 순서를 알 수 없으므로 **가정은 언제나 불리한 쪽**으로 잡는다.

- 갭은 시가로 체결한다. 유리한 갭도 그대로 인정하되, 불리한 갭도 그대로 맞는다
- 손절과 익절이 같은 봉에서 모두 닿으면 **손절을 택한다**. 순서를 모르는 이상 나쁜 쪽을 가정한다
- 거래정지·상한가 마감·하한가 마감·액션일에는 체결이 없다
- 손절은 장중 스톱이며 시장가에 가깝게 나가므로 호가 몇 틱만큼 불리하게 잡는다

가격은 전부 **1단 원본가 축**이다. 실제 호가는 원본가로 내기 때문이다 (스펙 §10.3).
"""

from dataclasses import dataclass
from datetime import date
from enum import Enum

from krx_sprint.backtest.costs import tick_size
from krx_sprint.backtest.params import ExecutionParams


class ExitReason(Enum):
    """청산 사유"""

    STOP_LOSS = "손절"
    TAKE_PROFIT = "익절"
    DELISTING = "폐지"
    EXPIRY = "만료"


@dataclass(frozen=True)
class DailyBar:
    """체결 판정에 필요한 하루치 (원본가 축)

    Attributes:
        open: 시가 (원)
        high: 고가 (원)
        low: 저가 (원)
        close: 종가 (원)
        value: 거래대금 (원). 유동성 상한 계산에 쓴다
        market: 시장 구분. 호가가격단위가 시장별로 달랐던 구간이 있다
        is_halted: 거래정지 — 매매 불가
        is_limit_up_close: 상한가 마감 — 매수 미체결
        is_limit_down_close: 하한가 마감 — 매도 미체결
        is_shares_jump: 상장주식수 급변 — 원본가 축이 바뀌어 전일 기준 지정가가 무의미하다
        is_last_seen: 1단 최종 등장일 — 다음 거래일이 없다
        is_unadjusted_action: 수정 미반영 액션 — 이 구간을 지난 트레이드는 수익률을 신뢰할 수 없다
    """

    open: int
    high: int
    low: int
    close: int
    value: int
    market: str
    is_halted: bool = False
    is_limit_up_close: bool = False
    is_limit_down_close: bool = False
    is_shares_jump: bool = False
    is_last_seen: bool = False
    is_unadjusted_action: bool = False


@dataclass(frozen=True)
class ExitFill:
    """청산 체결

    Attributes:
        reason: 청산 사유
        price: 체결가 (원)
    """

    reason: ExitReason
    price: int


def is_tradable(bar: DailyBar) -> bool:
    """그날 매매가 가능한지 판정한다.

    거래정지일은 체결 자체가 불가능하고(실측 3.61%), 액션일은 원본가 축이 바뀌어
    전일 기준으로 계산한 지정가가 의미를 잃는다.

    Args:
        bar: 대상 일봉

    Returns:
        매매 가능 여부
    """
    return not bar.is_halted and not bar.is_shares_jump


def fill_buy_limit(bar: DailyBar, limit_price: int) -> int | None:
    """매수 지정가의 체결 여부와 체결가를 구한다.

    시가가 지정가보다 낮으면(갭 하락) 시가에 체결된다. 장중 저가가 지정가에 닿으면
    지정가에 체결되고, 못 닿으면 미체결이다.

    상한가 마감일은 미체결로 본다 — 테마 대장주에서 특히 잦고, 이 처리가 빠지면
    "못 사는 종목을 원하는 가격에 샀다"는 가짜 수익이 생긴다.

    Args:
        bar: 대상 일봉
        limit_price: 매수 지정가 (원)

    Returns:
        체결가 (미체결이면 None)

    Raises:
        ValueError: 지정가가 0 이하인 경우
    """
    _require_positive_price(limit_price)

    if not is_tradable(bar) or bar.is_limit_up_close:
        return None

    if bar.open <= limit_price:
        return bar.open

    if bar.low <= limit_price:
        return limit_price

    return None


def fill_buy_stop(bar: DailyBar, stop_price: int) -> int | None:
    """역지정가 매수의 체결 여부와 체결가를 구한다.

    지정가 매수와 반대로 **가격이 올라와야** 체결된다. 이탈한 선을 되찾는 것을 확인하고 사는
    진입에 쓴다.

    시가가 이미 기준가 위면(갭 상승) 시가에 체결된다 — 역지정가는 그 가격을 지켜주지 않는다.

    Args:
        bar: 대상 일봉
        stop_price: 역지정가 (원)

    Returns:
        체결가 (미체결이면 None)

    Raises:
        ValueError: 역지정가가 0 이하인 경우
    """
    _require_positive_price(stop_price)

    if not is_tradable(bar) or bar.is_limit_up_close:
        return None

    if bar.open >= stop_price:
        return bar.open

    if bar.high >= stop_price:
        return stop_price

    return None


def fill_sell_limit(bar: DailyBar, limit_price: int) -> int | None:
    """매도 지정가(익절)의 체결 여부와 체결가를 구한다.

    Args:
        bar: 대상 일봉
        limit_price: 매도 지정가 (원)

    Returns:
        체결가 (미체결이면 None)

    Raises:
        ValueError: 지정가가 0 이하인 경우
    """
    _require_positive_price(limit_price)

    if not is_tradable(bar) or bar.is_limit_down_close:
        return None

    if bar.open >= limit_price:
        return bar.open

    if bar.high >= limit_price:
        return limit_price

    return None


def fill_stop_loss(
    bar: DailyBar,
    stop_price: int,
    trade_date: date,
    params: ExecutionParams,
) -> int | None:
    """장중 스톱 손절의 체결 여부와 체결가를 구한다 (설계 §13-③ 확정).

    저가가 손절가에 닿으면 그날 체결된다. 갭 하락으로 시가가 이미 손절가 아래면 시가가 기준이다 —
    스톱 주문은 그 가격을 지켜주지 않는다. 여기에 호가 몇 틱만큼 불리하게 더 뺀다.

    Args:
        bar: 대상 일봉
        stop_price: 손절가 (원)
        trade_date: 체결일 (호가가격단위 판정에 쓴다)
        params: 체결 가정

    Returns:
        체결가 (미체결이면 None)

    Raises:
        ValueError: 손절가가 0 이하인 경우
    """
    _require_positive_price(stop_price)

    if not is_tradable(bar):
        return None

    if bar.low > stop_price:
        return None

    base_price = min(bar.open, stop_price)
    slippage = tick_size(base_price, bar.market, trade_date) * params.stop_loss_slippage_ticks

    return base_price - slippage


def resolve_exit(
    bar: DailyBar,
    *,
    stop_price: int,
    target_price: int,
    trade_date: date,
    params: ExecutionParams | None = None,
) -> ExitFill | None:
    """그날의 청산 여부를 판정한다.

    **손절이 익절보다 우선한다.** 일봉으로는 장중 순서를 알 수 없으므로 보수적으로 잡는다.
    이 가정에 결과가 얼마나 의존하는지는 동시 터치 건수를 리포트에 출력해 드러낸다.

    Args:
        bar: 대상 일봉
        stop_price: 손절가 (원)
        target_price: 익절 목표가 (원)
        trade_date: 체결일
        params: 체결 가정 (생략 시 기본값)

    Returns:
        청산 체결 (청산 없으면 None)

    Raises:
        ValueError: 가격이 0 이하인 경우
    """
    execution_params = params if params is not None else ExecutionParams()

    stop_fill = fill_stop_loss(bar, stop_price, trade_date, execution_params)
    if stop_fill is not None:
        return ExitFill(ExitReason.STOP_LOSS, stop_fill)

    target_fill = fill_sell_limit(bar, target_price)
    if target_fill is not None:
        return ExitFill(ExitReason.TAKE_PROFIT, target_fill)

    return None


def max_quantity(bar: DailyBar, price: int, budget: int, params: ExecutionParams) -> int:
    """살 수 있는 최대 수량을 구한다 (설계 §7.4).

    배분 금액과 **당일 거래대금 참여율 상한** 중 작은 쪽이다. 대장주는 거래대금이 커서
    대부분 무해하지만, 소형주가 대장주로 잡히는 경우를 막는 안전장치다.

    Args:
        bar: 대상 일봉
        price: 체결 예정가 (원)
        budget: 배분 금액 (원)
        params: 체결 가정

    Returns:
        최대 수량 (주). 한 주도 못 사면 0

    Raises:
        ValueError: 가격이 0 이하인 경우
    """
    _require_positive_price(price)

    if budget <= 0:
        return 0

    liquidity_budget = int(bar.value * params.liquidity_participation_rate)

    return min(budget, liquidity_budget) // price


def _require_positive_price(price: int) -> None:
    """가격이 양수인지 확인한다.

    Args:
        price: 검사할 가격 (원)

    Raises:
        ValueError: 0 이하인 경우
    """
    if price <= 0:
        raise ValueError(f"가격은 0보다 커야 합니다: {price}")
