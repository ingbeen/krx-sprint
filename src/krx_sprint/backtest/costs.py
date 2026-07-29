"""매매 비용 모델

수수료·증권거래세·호가가격단위를 담당한다 (백테스트 설계 §8).
단타 전략은 회전율이 높아 비용이 순기대값의 부호를 가르므로, 세율은 기억이 아니라
출처로 확정한 값을 쓴다. 출처 목록은 설계 문서 부록에 있다.

핵심 계약은 두 가지다.

- **증권거래세는 결제일 기준이다.** 법령 시행일은 "양도(=결제)" 기준이므로, 체결일에
  세율을 매기려면 거래일 캘린더로 T+2를 환산해야 한다. 상수로는 시행일만 두고 환산은
  계산으로 한다 — 체결일 경계를 상수로 박으면 캘린더가 바뀔 때 조용히 어긋난다
- **호가가격단위는 체결일 기준이다.** 주문 시점 규칙이라 결제일 환산이 필요 없다.
  2023-01-25 개편으로 표본 구간 안에서 규칙이 한 번 바뀌며, 개편 전에는 시장별로도 달랐다

정렬·반올림은 언제나 **불리한 쪽**으로 한다. 매수 지정가는 내림, 매도 지정가는 올림,
비용은 원 단위 올림이다. 일봉 백테스트의 가정은 낙관 쪽으로 틀리면 안 된다.
"""

import math
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum

from krx_sprint.common_constants import COLLECTION_START_DATE, MARKETS

# 결제 지연 (거래일). 체결일 + 2 거래일이 결제일이다
SETTLEMENT_LAG_DAYS = 2

# 기본 수수료율 (비율, 0.00015 = 0.015%). 국내 온라인 일반 요율의 보수적 상단 (설계 §13-①).
# 매수·매도 양방향에 부과된다
DEFAULT_FEE_RATE = 0.00015

# 호가가격단위 개편 시행일 (설계 §8.3)
TICK_REFORM_DATE = date(2023, 1, 25)


@dataclass(frozen=True)
class TaxRate:
    """시장별 증권거래세율 구성

    합계만 쓰면 되지만 본세와 농어촌특별세를 나눠 보관한다 — 출처 대조가 가능해야
    나중에 세율이 바뀌었을 때 어디를 고칠지 알 수 있다.

    Attributes:
        base_rate: 증권거래세 본세 (비율, 0.0005 = 0.05%)
        surtax_rate: 농어촌특별세 (비율). 유가증권시장에만 부과된다
    """

    base_rate: float
    surtax_rate: float

    @property
    def total_rate(self) -> float:
        """실제로 매도금액에 물리는 합계 세율 (비율)."""
        return self.base_rate + self.surtax_rate


@dataclass(frozen=True)
class TaxPeriod:
    """법령 시행일 기준 세율 구간

    Attributes:
        effective_from: 시행일 (**결제일** 기준)
        kospi: 유가증권시장 세율
        kosdaq: 코스닥시장 세율
    """

    effective_from: date
    kospi: TaxRate
    kosdaq: TaxRate


# 증권거래세 스케줄 (설계 §8.1). 시행일은 모두 결제일 기준이다.
# 첫 구간의 세율은 표본 시작 이전부터 유지된 값이며, 표본 밖 구간은 모델링하지 않는다
SECURITIES_TAX_SCHEDULE: tuple[TaxPeriod, ...] = (
    TaxPeriod(COLLECTION_START_DATE, TaxRate(0.0015, 0.0015), TaxRate(0.0030, 0.0)),
    TaxPeriod(date(2019, 6, 3), TaxRate(0.0010, 0.0015), TaxRate(0.0025, 0.0)),
    TaxPeriod(date(2021, 1, 1), TaxRate(0.0008, 0.0015), TaxRate(0.0023, 0.0)),
    TaxPeriod(date(2023, 1, 1), TaxRate(0.0005, 0.0015), TaxRate(0.0020, 0.0)),
    TaxPeriod(date(2024, 1, 1), TaxRate(0.0003, 0.0015), TaxRate(0.0018, 0.0)),
    TaxPeriod(date(2025, 1, 1), TaxRate(0.0000, 0.0015), TaxRate(0.0015, 0.0)),
    TaxPeriod(date(2026, 1, 1), TaxRate(0.0005, 0.0015), TaxRate(0.0020, 0.0)),
)

# 호가가격단위 테이블. (상한 가격, 단위)를 오름차순으로 두고 상한이 None인 항목이 마지막이다.
# 판정은 "가격 < 상한"이므로 상한값 자체는 다음 구간에 속한다
TickTable = tuple[tuple[int | None, int], ...]

_TICKS_LEGACY_KOSPI: TickTable = (
    (1_000, 1),
    (5_000, 5),
    (10_000, 10),
    (50_000, 50),
    (100_000, 100),
    (500_000, 500),
    (None, 1_000),
)

# 개편 전 코스닥은 10만~50만원 구간이 100원으로 코스피(500원)와 달랐다
_TICKS_LEGACY_KOSDAQ: TickTable = (
    (1_000, 1),
    (5_000, 5),
    (10_000, 10),
    (50_000, 50),
    (500_000, 100),
    (None, 1_000),
)

# 개편 후에는 두 시장이 같다
_TICKS_CURRENT: TickTable = (
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
    (None, 1_000),
)

_LEGACY_TICK_TABLES: dict[str, TickTable] = {
    "KOSPI": _TICKS_LEGACY_KOSPI,
    "KOSDAQ": _TICKS_LEGACY_KOSDAQ,
}


class OrderSide(Enum):
    """주문 방향

    정렬 방향과 비용 구성이 달라지므로 문자열 대신 열거형으로 고정한다.
    """

    BUY = "매수"
    SELL = "매도"


@dataclass(frozen=True)
class TradeCost:
    """한 번의 체결에 드는 비용 (원)

    Attributes:
        fee: 매매 수수료
        tax: 증권거래세 (매수는 0)
    """

    fee: int
    tax: int

    @property
    def total(self) -> int:
        """총비용 (원)."""
        return self.fee + self.tax


def _validate_market(market: str) -> None:
    """수집 대상 시장인지 확인한다.

    Args:
        market: 시장 구분

    Raises:
        ValueError: 유니버스 밖 시장인 경우
    """
    if market not in MARKETS:
        raise ValueError(f"지원하지 않는 시장입니다 (유니버스는 {'/'.join(MARKETS)}): {market!r}")


def _tick_table(market: str, trade_date: date) -> TickTable:
    """체결일과 시장에 해당하는 호가가격단위 표를 고른다.

    Args:
        market: 시장 구분
        trade_date: 체결일

    Returns:
        호가가격단위 표

    Raises:
        ValueError: 지원하지 않는 시장인 경우
    """
    _validate_market(market)

    if trade_date >= TICK_REFORM_DATE:
        return _TICKS_CURRENT

    table = _LEGACY_TICK_TABLES.get(market)
    if table is None:
        raise RuntimeError(f"내부 불변조건 위반: 시장 검증을 통과했으나 호가 표가 없습니다 (market={market!r})")

    return table


def tick_size(price: int | float, market: str, trade_date: date) -> int:
    """해당 가격대의 호가가격단위를 구한다 (설계 §8.3).

    Args:
        price: 기준 가격 (원)
        market: 시장 구분
        trade_date: 체결일

    Returns:
        호가가격단위 (원)

    Raises:
        ValueError: 가격이 0 이하이거나 지원하지 않는 시장인 경우
    """
    if price <= 0:
        raise ValueError(f"가격은 0보다 커야 합니다: {price}")

    for upper, unit in _tick_table(market, trade_date):
        if upper is None or price < upper:
            return unit

    raise RuntimeError(f"내부 불변조건 위반: 호가 표에서 구간을 찾지 못했습니다 (price={price})")


def align_price(price: int | float, market: str, trade_date: date, *, side: OrderSide) -> int:
    """지정가를 실제 호가에 맞춘다.

    매수는 내림, 매도는 올림이다 — 체결 조건을 언제나 더 빡빡하게 만들어
    백테스트가 낙관 쪽으로 틀리지 않게 한다.

    Args:
        price: 정렬 전 가격 (원)
        market: 시장 구분
        trade_date: 체결일
        side: 주문 방향

    Returns:
        호가에 맞춘 가격 (원)

    Raises:
        ValueError: 가격이 0 이하이거나 지원하지 않는 시장인 경우
    """
    unit = tick_size(price, market, trade_date)

    if side is OrderSide.BUY:
        return int(math.floor(price / unit) * unit)

    return int(math.ceil(price / unit) * unit)


class CostModel:
    """거래일 캘린더에 묶인 비용 계산기

    결제일 환산에 캘린더가 필요하므로, 캘린더와 수수료율을 한 번 주입해 두고 쓴다.
    캘린더는 1단 수집 일자(= 실제 KRX 거래일)를 넘긴다.
    """

    def __init__(
        self,
        trading_days: Sequence[date],
        fee_rate: float = DEFAULT_FEE_RATE,
        *,
        include_tax: bool = True,
    ) -> None:
        """비용 계산기를 초기화한다.

        Args:
            trading_days: 거래일 (오름차순, 중복 없음)
            fee_rate: 매매 수수료율 (비율, 0.00015 = 0.015%)
            include_tax: 증권거래세를 물릴지 여부. 새니티 체크(무비용 실행)에서만 끈다

        Raises:
            ValueError: 캘린더가 비었거나 정렬되지 않았거나 수수료율이 음수인 경우
        """
        if not trading_days:
            raise ValueError("거래일 캘린더가 비어 있습니다")

        days = list(trading_days)
        if any(later <= earlier for earlier, later in zip(days, days[1:], strict=False)):
            raise ValueError("거래일 캘린더는 오름차순이어야 하며 중복이 없어야 합니다")

        if fee_rate < 0:
            raise ValueError(f"수수료율은 0 이상이어야 합니다: {fee_rate}")

        self._days = days
        self._positions = {day: index for index, day in enumerate(days)}
        self._fee_rate = fee_rate
        self._include_tax = include_tax

    @property
    def fee_rate(self) -> float:
        """매매 수수료율 (비율)."""
        return self._fee_rate

    def settlement_date(self, trade_date: date) -> date:
        """체결일의 결제일을 구한다 (거래일 기준 T+2).

        Args:
            trade_date: 체결일

        Returns:
            결제일

        Raises:
            ValueError: 체결일이 거래일이 아니거나 결제일이 캘린더 밖인 경우
        """
        position = self._positions.get(trade_date)
        if position is None:
            raise ValueError(f"거래일이 아닙니다: {trade_date.isoformat()}")

        settled = position + SETTLEMENT_LAG_DAYS
        if settled >= len(self._days):
            raise ValueError(
                f"결제일이 캘린더 범위를 벗어나 세율을 확정할 수 없습니다: " f"{trade_date.isoformat()} (캘린더 종료 {self._days[-1].isoformat()})"
            )

        return self._days[settled]

    def tax_components(self, trade_date: date, market: str) -> TaxRate:
        """체결일에 적용될 증권거래세 구성을 구한다.

        Args:
            trade_date: 체결일
            market: 시장 구분

        Returns:
            본세·농특세로 나뉜 세율

        Raises:
            ValueError: 거래일이 아니거나 지원하지 않는 시장이거나 스케줄 범위 밖인 경우
        """
        _validate_market(market)
        settled = self.settlement_date(trade_date)

        # 결제일이 속한 구간은 "시행일 <= 결제일"을 만족하는 마지막 항목이다.
        # 시행일 당일 결제분부터 새 세율이므로 bisect_right를 쓴다
        effective_dates = [period.effective_from for period in SECURITIES_TAX_SCHEDULE]
        position = bisect_right(effective_dates, settled)

        if position == 0:
            raise ValueError(f"세율 스케줄 시작({effective_dates[0].isoformat()}) 이전의 결제일입니다: {settled.isoformat()}")

        period = SECURITIES_TAX_SCHEDULE[position - 1]
        return period.kospi if market == "KOSPI" else period.kosdaq

    def tax_rate(self, trade_date: date, market: str) -> float:
        """체결일에 적용될 증권거래세 합계 세율을 구한다.

        Args:
            trade_date: 체결일
            market: 시장 구분

        Returns:
            합계 세율 (비율, 0.0020 = 0.20%)

        Raises:
            ValueError: 거래일이 아니거나 지원하지 않는 시장이거나 스케줄 범위 밖인 경우
        """
        return self.tax_components(trade_date, market).total_rate

    def buy_cost(self, price: int | float, quantity: int) -> TradeCost:
        """매수 비용을 구한다. 증권거래세는 매도에만 부과되므로 수수료뿐이다.

        Args:
            price: 체결가 (원)
            quantity: 체결 수량 (주)

        Returns:
            비용 구성

        Raises:
            ValueError: 가격이나 수량이 0 이하인 경우
        """
        amount = self._trade_amount(price, quantity)
        return TradeCost(fee=self._round_up(amount * self._fee_rate), tax=0)

    def sell_cost(self, price: int | float, quantity: int, trade_date: date, market: str) -> TradeCost:
        """매도 비용을 구한다 (수수료 + 증권거래세).

        Args:
            price: 체결가 (원)
            quantity: 체결 수량 (주)
            trade_date: 체결일
            market: 시장 구분

        Returns:
            비용 구성

        Raises:
            ValueError: 입력이 유효하지 않거나 세율을 확정할 수 없는 경우
        """
        amount = self._trade_amount(price, quantity)
        tax_rate = self.tax_rate(trade_date, market) if self._include_tax else 0.0

        return TradeCost(fee=self._round_up(amount * self._fee_rate), tax=self._round_up(amount * tax_rate))

    @staticmethod
    def _trade_amount(price: int | float, quantity: int) -> float:
        """체결 금액을 구한다.

        Args:
            price: 체결가 (원)
            quantity: 체결 수량 (주)

        Returns:
            체결 금액 (원)

        Raises:
            ValueError: 가격이나 수량이 0 이하인 경우
        """
        if price <= 0:
            raise ValueError(f"체결가는 0보다 커야 합니다: {price}")

        if quantity <= 0:
            raise ValueError(f"수량은 0보다 커야 합니다: {quantity}")

        return float(price) * quantity

    @staticmethod
    def _round_up(amount: float) -> int:
        """비용을 원 단위로 올림한다 (보수적 가정).

        Args:
            amount: 원 단위 금액

        Returns:
            올림한 금액 (원)
        """
        return int(math.ceil(amount))
