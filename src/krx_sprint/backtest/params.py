"""전략 파라미터

백테스트가 받는 자유도를 한곳에 모은다 (백테스트 설계 §4·§5·§6·§10.1).

**파라미터 예산**을 지키는 것이 이 모듈의 목적이다. 신호 로직은 과최적화의 온상이라
블록별 개수 상한을 두기로 했다 — 테마 4, 기준봉 3, 스윙·카운팅 2, 진입·손절·익절, 자금 2.

`StopLossKind` 같은 **열거형은 최적화 대상이 아니라 비교 대상**이다. 한 번의 실행은 하나를
고정해 쓰고 서로 다른 실행을 비교한다. 연속값 노브를 늘리지 않는다는 뜻이며, 이 구분이
예산의 취지를 지킨다.

유니버스 필터와 체결 가정은 전략의 자유도가 아니라 **게이트와 가정**이라 따로 담는다.
"""

from dataclasses import dataclass
from enum import Enum

from krx_sprint.backtest.costs import DEFAULT_FEE_RATE


class StopLossKind(Enum):
    """손절 방식 (설계 §6.2)

    대중적 지표만 쓴다 — 과최적화를 피하려면 손절선이 해석 가능해야 한다.
    """

    SWING_LOW = "스윙 전저점"
    MOVING_AVERAGE = "이동평균"
    FIXED = "고정 비율"
    BAND_FLOOR = "밴드 하한선"


class EntryPriceKind(Enum):
    """매수 지정가 산정 방식 (설계 §6.1)

    `MA_BAND_SPLIT`만 한 종목을 여러 번에 나눠 사고, 나머지는 한 번에 산다.
    `MA_RECLAIM`만 가격이 **올라올 때** 사고, 나머지는 내려올 때 산다.
    """

    CLOSE_DISCOUNT = "종가 대비 할인"
    MOVING_AVERAGE = "이동평균"
    PREVIOUS_LOW = "전일 저가"
    MA_BAND_SPLIT = "이동평균 밴드 분할"
    MA_RECLAIM = "이동평균 회복 확인"


@dataclass(frozen=True)
class StrategyParams:
    """전략 파라미터

    모든 비율은 0~1 소수다 (루트 CLAUDE.md 비율 표기 규칙).

    Attributes:
        correlation_window: 상관 계산 창 (거래일)
        correlation_threshold: 동조화 판정 상관 임계 (비율)
        co_move_rate: 동반 급등 기준 등락률 (비율, 0.05 = 5%)
        min_cluster_size: 테마로 인정할 최소 클러스터 크기. 1이면 단독 급등이 테마가 된다
        strength_top_k: 테마 강도의 거래대금 합산에 포함할 상위 종목 수.
            전체를 더하면 잡주가 여럿 붙었다는 이유만으로 합계가 커진다
        use_residual_correlation: 시장 팩터를 제거한 잔차로 상관을 볼지 여부
        value_surge_multiple: 기준봉 거래대금 배수 (자기 과거 평균 대비)
        value_average_window: 기준봉 거래대금 평균 창 (거래일, 당일 제외)
        base_bar_rise_rate: 기준봉 최소 상승률 (비율)
        min_trading_value: 기준봉 최소 거래대금 (원)
        swing_reversal_rate: 스윙 확정에 필요한 반전폭 (비율)
        base_bar_expiry_days: 기준봉 유효기간 (거래일)
        max_decline_count: 진입을 허용하는 최대 하락 차수. 3이면 3차부터 금지
        entry_price_kind: 매수 지정가 산정 방식
        entry_discount_rate: 종가 대비 할인 폭 (비율)
        entry_ma_period: 진입 지정가용 이동평균 기간 (거래일)
        entry_band_periods: 밴드 분할 매수의 경계가 되는 이동평균 기간 (짧은 것부터).
            이웃한 두 선 사이가 한 밴드이며, 가장 긴 선이 매수 하한이자 손절 기준선이다
        stop_loss_kind: 손절 방식
        stop_loss_rate: 손절 폭 (비율). 고정 방식은 진입가 대비, 전저점·밴드 방식은 기준선 아래 여유
        stop_loss_ma_period: 손절용 이동평균 기간 (거래일)
        reward_risk_ratio: 손익비. 익절폭 = 손절폭 × 이 값
        initial_equity: 초기 자본 (원)
        max_positions: 동시 보유 종목 수
        min_market_cap: 유니버스 최소 시가총액 (원)
        min_listed_days: 1단 최초 등장 후 최소 경과 거래일
    """

    # 테마 동조화 (설계 §4)
    correlation_window: int = 60
    correlation_threshold: float = 0.5
    co_move_rate: float = 0.05
    min_cluster_size: int = 4
    strength_top_k: int = 5
    use_residual_correlation: bool = True

    # 기준봉 (설계 §5.1)
    value_surge_multiple: float = 3.0
    value_average_window: int = 20
    base_bar_rise_rate: float = 0.10
    min_trading_value: int = 3_000_000_000

    # 스윙·카운팅 (설계 §5.2)
    swing_reversal_rate: float = 0.05
    base_bar_expiry_days: int = 20
    max_decline_count: int = 2

    # 진입·손절·익절 (설계 §6)
    entry_price_kind: EntryPriceKind = EntryPriceKind.MA_BAND_SPLIT
    entry_discount_rate: float = 0.03
    entry_ma_period: int = 5
    entry_band_periods: tuple[int, ...] = (5, 10, 20)
    stop_loss_kind: StopLossKind = StopLossKind.BAND_FLOOR
    stop_loss_rate: float = 0.05
    stop_loss_ma_period: int = 5
    reward_risk_ratio: float = 1.5

    # 자금 (설계 §6.4)
    initial_equity: int = 10_000_000
    max_positions: int = 1

    # 유니버스 게이트 (설계 §4.2)
    min_market_cap: int = 30_000_000_000
    min_listed_days: int = 60

    def __post_init__(self) -> None:
        """파라미터 유효성을 즉시 검증한다.

        Raises:
            ValueError: 범위를 벗어난 값이 있는 경우
        """
        self._require_positive_window("correlation_window", self.correlation_window)
        self._require_positive_window("value_average_window", self.value_average_window)
        self._require_positive_window("base_bar_expiry_days", self.base_bar_expiry_days)
        self._require_positive_window("entry_ma_period", self.entry_ma_period)
        self._require_positive_window("stop_loss_ma_period", self.stop_loss_ma_period)

        self._require_rate("correlation_threshold", self.correlation_threshold, minimum=-1.0)
        self._require_rate("co_move_rate", self.co_move_rate)
        self._require_rate("base_bar_rise_rate", self.base_bar_rise_rate)
        self._require_rate("swing_reversal_rate", self.swing_reversal_rate, allow_zero=False)
        self._require_rate("entry_discount_rate", self.entry_discount_rate)
        self._require_rate("stop_loss_rate", self.stop_loss_rate, allow_zero=False)

        if self.min_cluster_size < 1:
            raise ValueError(f"min_cluster_size는 1 이상이어야 합니다: {self.min_cluster_size}")

        if self.strength_top_k < 1:
            raise ValueError(f"strength_top_k는 1 이상이어야 합니다: {self.strength_top_k}")

        self._require_ascending_band_periods()

        if self.max_decline_count < 1:
            raise ValueError(f"max_decline_count는 1 이상이어야 합니다: {self.max_decline_count}")

        if self.value_surge_multiple <= 1:
            raise ValueError(f"value_surge_multiple은 1보다 커야 합니다: {self.value_surge_multiple}")

        if self.reward_risk_ratio <= 0:
            raise ValueError(f"reward_risk_ratio는 0보다 커야 합니다: {self.reward_risk_ratio}")

        if self.initial_equity <= 0:
            raise ValueError(f"initial_equity는 0보다 커야 합니다: {self.initial_equity}")

        if self.max_positions < 1:
            raise ValueError(f"max_positions는 1 이상이어야 합니다: {self.max_positions}")

        if self.min_market_cap < 0:
            raise ValueError(f"min_market_cap은 0 이상이어야 합니다: {self.min_market_cap}")

        if self.min_trading_value < 0:
            raise ValueError(f"min_trading_value는 0 이상이어야 합니다: {self.min_trading_value}")

        if self.min_listed_days < 0:
            raise ValueError(f"min_listed_days는 0 이상이어야 합니다: {self.min_listed_days}")

    def _require_ascending_band_periods(self) -> None:
        """밴드 경계 기간이 짧은 것부터 오름차순인지 확인한다.

        밴드는 이웃한 두 선 사이의 구간이므로 경계가 최소 두 개 있어야 하고, 순서가 어긋나면
        "짧은 선을 깨고 긴 선까지"라는 정의가 성립하지 않는다.

        Raises:
            ValueError: 경계가 둘 미만이거나 오름차순이 아닌 경우
        """
        periods = self.entry_band_periods

        if len(periods) < 2:
            raise ValueError(f"entry_band_periods는 두 개 이상이어야 합니다: {periods}")

        if any(period <= 0 for period in periods):
            raise ValueError(f"entry_band_periods는 모두 0보다 커야 합니다: {periods}")

        if list(periods) != sorted(set(periods)):
            raise ValueError(f"entry_band_periods는 중복 없이 오름차순이어야 합니다: {periods}")

    @staticmethod
    def _require_positive_window(name: str, value: int) -> None:
        """창 길이가 양수인지 확인한다.

        Args:
            name: 파라미터 이름
            value: 검사할 값

        Raises:
            ValueError: 0 이하인 경우
        """
        if value <= 0:
            raise ValueError(f"{name}은(는) 0보다 커야 합니다: {value}")

    @staticmethod
    def _require_rate(name: str, value: float, *, minimum: float = 0.0, allow_zero: bool = True) -> None:
        """비율이 허용 범위인지 확인한다.

        Args:
            name: 파라미터 이름
            value: 검사할 값 (비율)
            minimum: 허용 하한
            allow_zero: 0을 허용할지 여부

        Raises:
            ValueError: 범위를 벗어난 경우
        """
        if value < minimum or value > 1:
            raise ValueError(f"{name}은(는) {minimum} 이상 1 이하의 비율이어야 합니다: {value}")

        if not allow_zero and value == 0:
            raise ValueError(f"{name}은(는) 0보다 커야 합니다: {value}")


@dataclass(frozen=True)
class ExecutionParams:
    """체결·비용 가정 (설계 §7·§8)

    전략의 자유도가 아니라 **가정**이다. 최적화하지 않고 민감도 시나리오로만 흔든다.

    Attributes:
        fee_rate: 매매 수수료율 (비율). 매수·매도 양방향에 부과된다
        liquidity_participation_rate: 당일 거래대금 대비 최대 주문 비중 (비율)
        delisting_penalty_rate: 폐지 청산 시 추가로 깎는 비율. 정리매매는 훨씬 불리하다
        stop_loss_slippage_ticks: 손절 체결에 불리하게 적용할 호가 틱 수
        include_tax: 증권거래세를 물릴지 여부. 새니티 체크(무비용 실행)에서만 끈다
    """

    fee_rate: float = DEFAULT_FEE_RATE
    liquidity_participation_rate: float = 0.01
    delisting_penalty_rate: float = 0.0
    stop_loss_slippage_ticks: int = 1
    include_tax: bool = True

    def __post_init__(self) -> None:
        """가정값 유효성을 즉시 검증한다.

        Raises:
            ValueError: 범위를 벗어난 값이 있는 경우
        """
        if self.fee_rate < 0:
            raise ValueError(f"fee_rate는 0 이상이어야 합니다: {self.fee_rate}")

        if not 0 < self.liquidity_participation_rate <= 1:
            raise ValueError(f"liquidity_participation_rate는 0 초과 1 이하의 비율이어야 합니다: {self.liquidity_participation_rate}")

        if not 0 <= self.delisting_penalty_rate <= 1:
            raise ValueError(f"delisting_penalty_rate는 0 이상 1 이하의 비율이어야 합니다: {self.delisting_penalty_rate}")

        if self.stop_loss_slippage_ticks < 0:
            raise ValueError(f"stop_loss_slippage_ticks는 0 이상이어야 합니다: {self.stop_loss_slippage_ticks}")
