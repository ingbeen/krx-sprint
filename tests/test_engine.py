"""engine 테스트

일별 루프·포지션·자금 관리의 계약을 고정한다 (백테스트 설계 §6·§7).

핵심 계약은 네 가지다.
- 신호는 D일 종가까지로 만들고 **체결은 D+1 봉에서만** 일어난다 (look-ahead 금지)
- 지표는 수정주가 축, 주문 가격은 원본가 축이다. 축 변환 계수는 신호일 값만 쓴다
- 동시 보유 상한과 **같은 클러스터 중복 진입 금지**를 지킨다
- 여러 테마가 후보면 **강도 1위부터** 사고, 주문 수는 남은 슬롯을 넘지 않는다
"""

from dataclasses import replace
from datetime import date, timedelta

import pandas as pd
import pytest

from krx_sprint.backtest.engine import run_backtest
from krx_sprint.backtest.params import EntryPriceKind, ExecutionParams, StopLossKind, StrategyParams
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
    stop_loss_kind=StopLossKind.FIXED,
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


def _panel(
    closes: dict[str, list[int]],
    values: dict[str, list[int]] | None = None,
    lows: dict[str, list[int]] | None = None,
) -> pd.DataFrame:
    """종가열로 통합 패널을 만든다.

    시가·고가·저가는 기본적으로 종가와 같아 체결 판정이 종가에만 의존한다. `lows`를 주면 그날의
    저가만 따로 내려 장중에만 닿는 지정가를 시험할 수 있다.
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
            low = (lows or {}).get(ticker, series)[index]
            rows.append(
                {
                    "date": pd.Timestamp(days[index]),
                    "ticker": ticker,
                    "market": "KOSPI",
                    "open": close,
                    "high": close,
                    "low": low,
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


# 두 테마가 같은 날 서는 시나리오 — 초반 흔들림을 달리해 잔차 상관이 갈리게 한다.
# 강한 쪽(STRONG)은 거래대금이 약한 쪽(WEAK)보다 크다
STRONG_LEADER, STRONG_PEER = "000001", "000002"
WEAK_LEADER, WEAK_PEER = "000003", "000004"
MARKET = "000009"


def _two_theme_closes() -> dict[str, list[int]]:
    """두 테마가 각각 기준봉과 1차 하락을 만드는 종가열을 준다."""
    tail = [1_100, 1_200]
    strong = [1_000, 1_005, 995, 1_200, 1_100, 1_050, *tail]
    weak = [1_000, 990, 1_010, 1_200, 1_090, 1_040, *tail]

    return {
        STRONG_LEADER: strong,
        STRONG_PEER: strong,
        WEAK_LEADER: weak,
        WEAK_PEER: weak,
        MARKET: [1_000] * len(strong),
    }


def _two_theme_values() -> dict[str, list[int]]:
    """강한 테마 쪽 거래대금을 크게 준다 (강도 점수가 갈리게 한다)."""
    base = [100_000_000] * 3 + [1_000_000_000] + [100_000_000] * 20
    multiples = {STRONG_LEADER: 9, STRONG_PEER: 8, WEAK_LEADER: 2, WEAK_PEER: 1, MARKET: 1}

    return {ticker: [value * multiple for value in base] for ticker, multiple in multiples.items()}


def _run_two_themes(max_positions: int):
    """두 테마 시나리오를 지정한 슬롯 수로 실행한다."""
    params = replace(PARAMS, max_positions=max_positions)
    panel = _panel(_two_theme_closes(), _two_theme_values())

    return run_backtest(panel, _trading_days(40), params, EXECUTION)


# 밴드 분할 매수 시나리오 — 20일 평탄 뒤 기준봉이 서야 5·10·20일선이 정배열로 남는다.
# 급등 직후 얕게 눌린 자리가 밴드 매수 구간이다
BAND_PARAMS = replace(
    PARAMS,
    entry_price_kind=EntryPriceKind.MA_BAND_SPLIT,
    stop_loss_kind=StopLossKind.BAND_FLOOR,
    entry_band_periods=(5, 10, 20),
    max_positions=1,
)

# 신호일(마지막 평탄 구간 + 기준봉 + 눌림 2일)의 5·10·20일선은 1110·1055·1027.5이며,
# 밴드 분할 지정가는 호가 정렬 후 1091·1073·1045·1036 이다
BAND_LIMIT_PRICES = (1_091, 1_073, 1_045, 1_036)


def _band_closes(tail: list[int]) -> dict[str, list[int]]:
    """20일 평탄 → 기준봉 → 눌림 → `tail` 로 이어지는 종가열을 만든다.

    시장 팩터 역할의 종목은 오르내려야 한다 — 완전히 평탄하면 잔차가 0이 돼 클러스터가 서지 않는다.
    """
    core = [1_000] * 20 + [1_200, 1_150, 1_100]
    length = len(core) + len(tail)

    return {
        LEADER: [*core, *tail],
        PEER: [*core, *tail],
        OTHER: [1_000 if index % 2 == 0 else 1_010 for index in range(length)],
    }


def _band_values(length: int) -> dict[str, list[int]]:
    """기준봉 일자에만 거래대금이 급증하도록 만든다."""
    series = [100_000_000] * 20 + [1_000_000_000] + [100_000_000] * (length - 21)

    return {ticker: series for ticker in (LEADER, PEER, OTHER)}


def _run_band(tail: list[int], lows: list[int] | None = None):
    """밴드 분할 시나리오를 실행한다. `lows`는 tail 구간의 저가다."""
    closes = _band_closes(tail)
    length = len(closes[LEADER])
    low_series = None

    if lows is not None:
        leader_lows = [*closes[LEADER][: length - len(lows)], *lows]
        low_series = {LEADER: leader_lows, PEER: leader_lows, OTHER: closes[OTHER]}

    panel = _panel(closes, _band_values(length), low_series)

    return run_backtest(panel, _trading_days(40), BAND_PARAMS, EXECUTION)


def _has_overlap(result) -> bool:
    """보유 구간이 겹치는 트레이드 쌍이 있는지 본다."""
    spans = sorted((trade.entry_date, trade.exit_date) for trade in result.trades)

    return any(spans[index + 1][0] < spans[index][1] for index in range(len(spans) - 1))


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


class TestThemeSelection:
    """여러 테마 중 무엇을 살지의 계약을 고정한다."""

    def test_strongest_theme_leader_is_bought(self):
        """
        목적: 슬롯이 하나면 **강도 1위 테마의 대장주**를 산다 — 선택과 집중이 전략의 전제다.

        Given: 같은 날 두 테마가 서고 한쪽 거래대금이 크게 우위인 시나리오
        When: 슬롯 1개로 run_backtest 실행
        Then: 강한 테마의 대장주만 원장에 남는다
        """
        # Given / When
        result = _run_two_themes(1)

        # Then
        assert result.trades
        assert {trade.ticker for trade in result.trades} == {STRONG_LEADER}

    def test_daily_orders_never_exceed_free_slots(self):
        """
        목적: 주문 수는 **남은 슬롯 수를 넘지 않는다** — 슬롯보다 많이 만들어 순회 순서로 버리면
        무엇을 살지가 선택 기준 없이 정해진다.

        Given: 두 테마가 동시에 후보가 되는 시나리오
        When: 슬롯을 1개와 2개로 각각 실행
        Then: 슬롯이 적을수록 주문이 적고, 슬롯 부족으로 버려진 주문이 없다
        """
        # Given / When
        single = _run_two_themes(1)
        double = _run_two_themes(2)

        # Then
        assert single.diagnostics.order_count < double.diagnostics.order_count
        assert single.diagnostics.slot_blocked == 0
        assert double.diagnostics.slot_blocked == 0

    def test_single_slot_holds_one_position_at_a_time(self):
        """
        목적: 슬롯이 하나면 보유 구간이 겹치지 않는다 — 동시 보유 상한의 불변조건이다.

        Given: 두 테마가 동시에 후보가 되는 시나리오 (슬롯 2개면 실제로 겹친다)
        When: 슬롯을 1개와 2개로 각각 실행
        Then: 1개일 때만 보유 구간이 겹치지 않는다
        """
        # Given / When
        single = _run_two_themes(1)
        double = _run_two_themes(2)

        # Then
        assert not _has_overlap(single)
        assert _has_overlap(double)


class TestBandSplitEntry:
    """이동평균 밴드 분할 매수의 계약을 고정한다."""

    def test_fills_stop_at_the_lowest_reached_price(self):
        """
        목적: 저가가 닿은 자리까지만 체결된다 — 닿지 않은 아래 자리는 남는다.

        Given: 밴드 지정가 1091·1073·1045·1036 중 저가가 1050까지만 내려온 날
        When: run_backtest 실행
        Then: 위 두 자리만 체결돼 체결 횟수가 2다
        """
        # Given / When
        result = _run_band([1_095, 1_300, 1_300], lows=[1_050, 1_300, 1_300])

        # Then
        assert len(result.trades) == 1
        assert result.trades[0].fill_count == 2

    def test_average_price_is_derived_from_actual_fills(self):
        """
        목적: 평균단가는 **투입금액 ÷ 수량**이다 — 반올림한 평균에서 되돌리면 손익에 오차가 생긴다.

        Given: 1091원에 2,291주·1073원에 2,329주가 체결되는 시나리오
        When: run_backtest 실행
        Then: 투입금액·수량·평균단가가 손계산과 일치한다
        """
        # Given / When
        trade = _run_band([1_095, 1_300, 1_300], lows=[1_050, 1_300, 1_300]).trades[0]

        # Then
        assert trade.quantity == 2_291 + 2_329
        assert trade.invested == 1_091 * 2_291 + 1_073 * 2_329
        assert trade.avg_entry_price == trade.invested // trade.quantity

    def test_deeper_pullback_fills_the_remaining_slices(self):
        """
        목적: 다음 날 더 눌리면 **남은 분할을 마저 담는다** — 추가 매수는 매일 다시 걸린다.

        Given: 이틀에 걸쳐 밴드 아래까지 저가가 내려오는 시나리오
        When: run_backtest 실행
        Then: 네 자리가 모두 체결되고 평균단가가 첫날보다 낮아진다
        """
        # Given / When
        shallow = _run_band([1_095, 1_300, 1_300], lows=[1_050, 1_300, 1_300]).trades[0]
        deep = _run_band([1_095, 1_060, 1_300], lows=[1_050, 1_030, 1_300]).trades[0]

        # Then
        assert deep.fill_count == len(BAND_LIMIT_PRICES)
        assert deep.avg_entry_price < shallow.avg_entry_price

    def test_refill_is_cancelled_once_the_position_closes(self):
        """
        목적: 청산되면 남은 분할 주문은 **취소**된다 — 끝난 트레이드의 손절가를 물려받은 포지션이
        새로 열리면 안 된다.

        Given: 진입 다음 날 익절되고, 그날 저가가 남은 분할 자리까지 내려오는 시나리오
        When: run_backtest 실행
        Then: 트레이드는 한 건이고 취소된 추가매수가 잡힌다
        """
        # Given / When
        result = _run_band([1_095, 1_300, 1_300], lows=[1_050, 1_000, 1_300])

        # Then
        assert len(result.trades) == 1
        assert result.diagnostics.cancelled_refills > 0

    def test_inverted_moving_averages_block_entry(self):
        """
        목적: 짧은 이동평균이 긴 것보다 아래면(역배열) 사지 않는다 — 눌림목이 아니라 추세 하락이다.

        Given: 기준봉 다음 날 깊게 무너져 5·10·20일선이 역배열이 된 시나리오
        When: run_backtest 실행
        Then: 주문이 하나도 만들어지지 않는다
        """
        # Given
        closes = _band_closes([])
        core = [1_000] * 20 + [1_200, 850, 840, 830]
        closes[LEADER] = core
        closes[PEER] = core
        closes[OTHER] = closes[OTHER][: len(core)]

        # When
        result = run_backtest(
            _panel(closes, _band_values(len(core))), _trading_days(40), BAND_PARAMS, EXECUTION
        )

        # Then
        assert result.diagnostics.signal_count > 0
        assert result.diagnostics.order_count == 0


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
