"""신호 로직 — 기준봉·스윙·N차 하락 카운팅·이동평균 밴드

백테스트 설계 §5를 코드로 옮긴다.

**look-ahead 방지가 이 모듈의 최우선 계약이다.** ZigZag 스윙은 본질적으로 사후 확정 지표라
"이 날이 스윙 하이였다"는 사실은 이후 반전이 나온 뒤에야 알 수 있다. 그래서 `Swing`은
발생 위치(`position`)와 **확정 위치(`confirmed_position`)를 나눠 들고**, 카운팅은 확정 위치에서만
반영한다. 이 구분을 없애면 미래 정보가 조용히 새고 성과만 좋아진다.

가격 축도 나뉜다 — 기준봉은 거래대금·등락률이라 **1단 원본가**, 스윙은 **2단 수정주가**를 쓴다
(스펙 §10.3). 이 모듈은 호출자가 넘긴 값을 그대로 믿으므로 축을 섞지 않는 책임은 호출자에게 있다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

import pandas as pd

from krx_sprint.backtest.params import StrategyParams
from krx_sprint.collect.adjusted_quality import PERCENT_TO_RATE
from krx_sprint.common_constants import (
    COL_CHANGE_RATE,
    COL_IS_HALTED,
    COL_IS_SHARES_JUMP,
    COL_IS_UNADJUSTED_ACTION,
    COL_NO_REGULAR_SESSION,
    COL_VALUE,
)


class SwingKind(Enum):
    """스윙 종류"""

    HIGH = "고점"
    LOW = "저점"


@dataclass(frozen=True)
class Swing:
    """확정된 스윙

    Attributes:
        kind: 고점인지 저점인지
        position: 스윙이 실제로 발생한 행 위치
        price: 스윙 가격
        confirmed_position: 확정된 행 위치. **이 위치부터만 신호에 쓸 수 있다**
    """

    kind: SwingKind
    position: int
    price: float
    confirmed_position: int


def find_confirmed_swings(prices: Sequence[float], reversal_rate: float) -> tuple[Swing, ...]:
    """ZigZag 방식으로 확정된 스윙을 찾는다 (설계 §5.2).

    직전 극값에서 `reversal_rate` 이상 되돌려야 그 극값을 스윙으로 확정한다.
    확정 위치는 되돌림이 관측된 위치이며 언제나 발생 위치보다 뒤다.

    **관측 구간의 첫 행(위치 0)에서 나온 스윙은 버린다.** 그 지점이 진짜 극값이었는지는
    이전 구간을 봐야 알 수 있는데 우리에겐 없다. 경계 효과로 가짜 스윙을 만들지 않기 위함이다.

    Args:
        prices: 종가열 (일자 오름차순)
        reversal_rate: 확정에 필요한 반전 비율 (0.05 = 5%)

    Returns:
        확정된 스윙 (확정 위치 오름차순)

    Raises:
        ValueError: 반전 비율이 0 이하이거나 1을 넘는 경우
    """
    if not 0 < reversal_rate <= 1:
        raise ValueError(f"반전 비율은 0 초과 1 이하여야 합니다: {reversal_rate}")

    if len(prices) < 2:
        return ()

    swings: list[Swing] = []
    high_position, high_price = 0, float(prices[0])
    low_position, low_price = 0, float(prices[0])
    rising: bool | None = None

    for position in range(1, len(prices)):
        price = float(prices[position])

        if rising is None or rising:
            if price > high_price:
                high_position, high_price = position, price
            elif price <= high_price * (1 - reversal_rate):
                swings.append(Swing(SwingKind.HIGH, high_position, high_price, position))
                rising = False
                low_position, low_price = position, price
                continue

        if rising is None or not rising:
            if price < low_price:
                low_position, low_price = position, price
            elif price >= low_price * (1 + reversal_rate):
                swings.append(Swing(SwingKind.LOW, low_position, low_price, position))
                rising = True
                high_position, high_price = position, price

    # 첫 행에서 나온 스윙은 경계 효과이므로 버린다
    return tuple(swing for swing in swings if swing.position > 0)


def count_declines(
    prices: Sequence[float],
    base_bar_position: int,
    params: StrategyParams,
    *,
    invalidate_below: float | None = None,
) -> tuple[int, ...]:
    """기준봉 이후의 N차 하락 차수를 위치별로 센다 (설계 §5.2).

    카운트는 **확정된 스윙 하이에서만** 올라간다. 진행 중인 하락은 아직 세지 않는다.
    **신고가를 갱신하면 0으로 리셋한다** (설계 §13-② 확정) — "N차가 깊을수록 위험"의 근거는
    고점 갱신 *실패*의 누적이므로, 갱신은 그 카운터를 무효화하는 사건이다.

    다음 경우에는 0을 돌려준다 (그 기준봉이 무효라는 뜻이다).
    기준봉 이전 구간 / 유효기간 경과 / `invalidate_below` 이탈 이후.

    Args:
        prices: 수정주가 종가열 (일자 오름차순)
        base_bar_position: 기준봉의 행 위치
        params: 전략 파라미터 (반전 비율·유효기간을 쓴다)
        invalidate_below: 이 가격을 종가가 밑돌면 기준봉을 무효화한다 (보통 기준봉 시가)

    Returns:
        위치별 하락 차수

    Raises:
        ValueError: 기준봉 위치가 구간을 벗어난 경우
    """
    if not 0 <= base_bar_position < len(prices):
        raise ValueError(f"기준봉 위치가 구간을 벗어났습니다: {base_bar_position} (길이 {len(prices)})")

    confirmations = {
        swing.confirmed_position
        for swing in find_confirmed_swings(prices, params.swing_reversal_rate)
        if swing.kind is SwingKind.HIGH
    }

    counts = [0] * len(prices)
    running_high = float(prices[base_bar_position])
    count = 0
    invalidated = False

    for position in range(base_bar_position, len(prices)):
        price = float(prices[position])

        if invalidate_below is not None and price < invalidate_below:
            invalidated = True

        if invalidated or position - base_bar_position > params.base_bar_expiry_days:
            counts[position] = 0
            continue

        # 신고가 갱신 → 랠리 재개로 보고 카운터를 되돌린다
        if price > running_high:
            running_high = price
            count = 0

        if position in confirmations:
            count += 1

        counts[position] = count

    return tuple(counts)


def next_day_moving_average(prices: Sequence[float], position: int, window: int) -> float | None:
    """다음 거래일의 이동평균을 구한다.

    주문 가격은 신호일 저녁에 확정되는데 다음 거래일 종가는 아직 없다. 그래서 **신호일 종가가
    그대로 이어진다고 가정**하고 창의 마지막 칸을 채운다. 신호일까지의 값만 읽으므로 PIT가
    깨지지 않는다.

    0 이하 종가(거래정지)가 창에 섞이면 평균이 의미를 잃으므로 값을 주지 않는다.

    Args:
        prices: 수정주가 종가열 (일자 오름차순)
        position: 신호일의 행 위치
        window: 이동평균 기간 (거래일)

    Returns:
        다음 거래일의 이동평균 (창을 못 채우면 None)

    Raises:
        ValueError: 기간이 0 이하이거나 신호일 위치가 구간을 벗어난 경우
    """
    if window <= 0:
        raise ValueError(f"이동평균 기간은 0보다 커야 합니다: {window}")

    if not 0 <= position < len(prices):
        raise ValueError(f"신호일 위치가 구간을 벗어났습니다: {position} (길이 {len(prices)})")

    # 창의 마지막 칸은 신호일 종가로 채우므로 과거는 window - 1 개만 있으면 된다
    if position + 1 < window - 1:
        return None

    history = [float(value) for value in prices[position + 2 - window : position + 1]]
    window_values = [*history, float(prices[position])]

    if any(value <= 0 for value in window_values):
        return None

    return sum(window_values) / window


def band_split_prices(bounds: Sequence[float], orders_per_band: int) -> tuple[float, ...]:
    """밴드 경계 사이를 균등 분할한 매수 가격을 만든다.

    경계선 **자체가 아니라 경계 사이의 내부점**을 쓴다. 선이 깨진 뒤에 사는 것이 규칙이므로
    선 위에서 받치는 주문은 이 전략의 자리가 아니다.

    경계는 가격이 높은 쪽부터 순서대로 내려와야 한다. 짧은 이동평균이 긴 것보다 아래에 있으면
    (역배열) 눌림목이 아니라 추세 하락이므로 매수 자리를 만들지 않는다.

    Args:
        bounds: 밴드 경계 가격 (짧은 이동평균부터). 순 내림차순이어야 한다
        orders_per_band: 밴드 하나당 지정가 개수

    Returns:
        분할 지정가 (내림차순). 역배열이면 빈 튜플

    Raises:
        ValueError: 경계가 둘 미만이거나 밴드당 개수가 0 이하인 경우
    """
    if len(bounds) < 2:
        raise ValueError(f"밴드 경계는 두 개 이상이어야 합니다: {len(bounds)}개")

    if orders_per_band <= 0:
        raise ValueError(f"밴드당 지정가 개수는 0보다 커야 합니다: {orders_per_band}")

    values = [float(bound) for bound in bounds]
    if any(values[index] <= values[index + 1] for index in range(len(values) - 1)):
        return ()

    prices: list[float] = []
    for upper, lower in zip(values, values[1:], strict=False):
        step = (upper - lower) / (orders_per_band + 1)
        prices.extend(upper - step * offset for offset in range(1, orders_per_band + 1))

    return tuple(prices)


def mark_base_bars(series: pd.DataFrame, params: StrategyParams) -> pd.Series:
    """기준봉을 판정한다 (설계 §5.1).

    `거래대금 >= X × 직전 N일 평균` AND `등락률 >= Y` AND `거래대금 >= 하한`이며,
    상장주식수 급변일과 거래정지일은 제외한다.

    평균 창에 **당일을 포함하지 않는다** — 포함하면 급증 조건이 자기 자신에 희석된다.
    창을 채우지 못한 초반 구간은 판정하지 않는다(결측을 통과로 취급하지 않는다).

    거래대금·등락률은 **1단 원본가**다. 수정주가로 계산하면 안 된다 (스펙 §10.3).

    Args:
        series: 한 종목의 시계열 (일자 오름차순). 거래대금·등락률·플래그 컬럼이 필요하다
        params: 전략 파라미터

    Returns:
        행별 기준봉 여부

    Raises:
        ValueError: 필요한 컬럼이 없는 경우
    """
    required = (COL_VALUE, COL_CHANGE_RATE, COL_IS_SHARES_JUMP, COL_IS_HALTED)
    missing = [column for column in required if column not in series.columns]
    if missing:
        raise ValueError(f"기준봉 판정에 필요한 컬럼이 없습니다: {missing}")

    values = series[COL_VALUE]
    # shift(1) 로 당일을 뺀 뒤 평균을 낸다
    average = values.shift(1).rolling(params.value_average_window).mean()
    rise_rate = series[COL_CHANGE_RATE] / PERCENT_TO_RATE

    marked = (
        (values >= params.value_surge_multiple * average)
        & (rise_rate >= params.base_bar_rise_rate)
        & (values >= params.min_trading_value)
        & ~series[COL_IS_SHARES_JUMP]
        & ~series[COL_IS_HALTED]
    )

    return marked.astype(bool)


def swing_mask(frame: pd.DataFrame) -> pd.Series:
    """스윙·고저 계산에 쓸 수 있는 행을 표시한다 (스펙 §8.1·§8.2).

    제외 대상은 세 가지다.
    정규장 미형성(고가·저가가 0이라 가짜 저점이 생긴다) / 수정 미반영 액션(가짜 갭) /
    거래정지(가격이 0이다).

    Args:
        frame: 대상 시계열. 플래그 컬럼이 필요하다

    Returns:
        행별 사용 가능 여부

    Raises:
        ValueError: 필요한 컬럼이 없는 경우
    """
    required = (COL_NO_REGULAR_SESSION, COL_IS_UNADJUSTED_ACTION, COL_IS_HALTED)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"스윙 판정에 필요한 컬럼이 없습니다: {missing}")

    excluded = frame[COL_NO_REGULAR_SESSION] | frame[COL_IS_UNADJUSTED_ACTION] | frame[COL_IS_HALTED]
    return (~excluded).astype(bool)
