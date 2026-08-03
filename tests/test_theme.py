"""theme 테스트

테마 동조화 클러스터링의 계약을 고정한다 (백테스트 설계 §4, 확정안 B).

핵심 계약은 네 가지다.
- **단독 급등은 테마가 아니다.** 2~3등주가 함께 움직여야 테마다
- 시장 팩터를 제거한 **잔차 상관**으로 본다. 지수 전체가 오른 날 전 종목이 한 테마가 되면 안 된다
- 대장주는 클러스터 안에서 당일 **거래대금(원본가)** 1위다
- 클러스터는 **강도 점수 내림차순**으로 나온다. 매매 대상이 하나뿐이라 순서가 곧 선택이다
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from krx_sprint.backtest.params import StrategyParams
from krx_sprint.backtest.theme import find_clusters
from krx_sprint.common_constants import (
    COL_ADJ_CLOSE,
    COL_CHANGE_RATE,
    COL_DATE,
    COL_IS_HALTED,
    COL_IS_SHARES_JUMP,
    COL_MARKET,
    COL_MARKET_CAP,
    COL_TICKER,
    COL_VALUE,
)

START_DATE = date(2024, 6, 3)
BASE_PRICE = 1_000.0

# 서로 다른 성격의 일간 수익률 패턴 (결정적 — 랜덤을 쓰지 않는다)
RETURNS_A = [0.01, -0.01, 0.02, -0.02, 0.01, 0.10]
RETURNS_C = [-0.01, 0.01, 0.01, -0.01, -0.01, 0.02]
RETURNS_D = [0.005, 0.005, -0.005, 0.005, -0.005, 0.01]

DEFAULT_PARAMS = StrategyParams(
    correlation_window=6,
    correlation_threshold=0.5,
    co_move_rate=0.05,
    min_cluster_size=2,
    min_market_cap=0,
    min_trading_value=0,
    min_listed_days=0,
)


def _prices(returns: list[float]) -> list[float]:
    """수익률열을 종가열로 바꾼다 (첫 종가는 기준가)."""
    prices = [BASE_PRICE]
    for rate in returns:
        prices.append(prices[-1] * (1 + rate))
    return prices


def _panel(
    returns_by_ticker: dict[str, list[float]],
    change_rates: dict[str, float],
    values: dict[str, int] | None = None,
    flags: dict[str, dict[str, bool]] | None = None,
    market_caps: dict[str, int] | None = None,
    surge_position: int | None = None,
) -> pd.DataFrame:
    """클러스터링에 필요한 최소 패널을 만든다.

    `surge_position`(기본: 마지막 행)이 신호일이며, `change_rates`는 그날의 등락률(% 단위)이다.
    """
    rows: list[dict[str, object]] = []

    for ticker, returns in returns_by_ticker.items():
        prices = _prices(returns)
        signal_position = surge_position if surge_position is not None else len(prices) - 1
        for index, price in enumerate(prices):
            is_last = index == signal_position
            ticker_flags = (flags or {}).get(ticker, {})
            rows.append(
                {
                    COL_DATE: pd.Timestamp(START_DATE + timedelta(days=index)),
                    COL_TICKER: ticker,
                    COL_MARKET: "KOSDAQ",
                    COL_VALUE: (values or {}).get(ticker, 1_000_000_000) if is_last else 1_000_000,
                    COL_CHANGE_RATE: change_rates[ticker] if is_last else 0.0,
                    COL_MARKET_CAP: (market_caps or {}).get(ticker, 100_000_000_000),
                    COL_ADJ_CLOSE: price,
                    COL_IS_HALTED: ticker_flags.get(COL_IS_HALTED, False) if is_last else False,
                    COL_IS_SHARES_JUMP: ticker_flags.get(COL_IS_SHARES_JUMP, False) if is_last else False,
                }
            )

    return pd.DataFrame(rows)


def _target_date(panel: pd.DataFrame) -> date:
    """패널의 마지막 일자를 신호일로 쓴다."""
    return panel[COL_DATE].max().date()


class TestClusterFormation:
    """클러스터 성립 조건을 고정한다."""

    def test_single_surge_is_not_a_theme(self):
        """
        목적: **단독 급등은 테마가 아니다** — 전략의 핵심 전제다.

        Given: A만 +10% 급등하고 나머지는 잠잠한 날
        When: find_clusters 호출
        Then: 클러스터가 없다
        """
        # Given
        panel = _panel(
            {"000001": RETURNS_A, "000002": RETURNS_C, "000003": RETURNS_D},
            change_rates={"000001": 10.0, "000002": 2.0, "000003": 1.0},
        )

        # When
        clusters = find_clusters(panel, _target_date(panel), DEFAULT_PARAMS)

        # Then
        assert clusters == ()

    def test_correlated_pair_forms_a_cluster(self):
        """
        목적: 함께 움직여 온 종목들이 같은 날 동반 급등하면 테마가 된다.

        Given: A와 B가 같은 수익률 이력을 갖고 둘 다 +10% 급등
        When: find_clusters 호출
        Then: 두 종목이 한 클러스터로 묶인다
        """
        # Given
        panel = _panel(
            {"000001": RETURNS_A, "000002": RETURNS_A, "000003": RETURNS_C, "000004": RETURNS_D},
            change_rates={"000001": 10.0, "000002": 10.0, "000003": 2.0, "000004": 1.0},
        )

        # When
        clusters = find_clusters(panel, _target_date(panel), DEFAULT_PARAMS)

        # Then
        assert len(clusters) == 1
        assert set(clusters[0].tickers) == {"000001", "000002"}

    def test_leader_is_top_trading_value(self):
        """
        목적: 대장주는 클러스터 내 당일 **거래대금 1위**다 (원본가 1단 기준).

        Given: 동반 급등한 두 종목의 거래대금이 다름
        When: find_clusters 호출
        Then: 거래대금이 큰 쪽이 대장주다
        """
        # Given
        panel = _panel(
            {"000001": RETURNS_A, "000002": RETURNS_A, "000003": RETURNS_C},
            change_rates={"000001": 10.0, "000002": 10.0, "000003": 1.0},
            values={"000001": 5_000_000_000, "000002": 9_000_000_000},
        )

        # When
        clusters = find_clusters(panel, _target_date(panel), DEFAULT_PARAMS)

        # Then
        assert clusters[0].leader == "000002"


class TestMarketFactor:
    """시장 팩터 제거의 효과를 고정한다."""

    def test_market_wide_surge_forms_no_cluster(self):
        """
        목적: 지수 전체가 똑같이 오른 날 전 종목이 한 테마가 되면 안 된다.

        Given: 모든 종목이 완전히 같은 이력으로 함께 급등한 날 (= 순수 시장 움직임)
        When: 잔차 상관을 켠 채 find_clusters 호출
        Then: 잔차가 남지 않아 클러스터가 생기지 않는다
        """
        # Given
        panel = _panel(
            {"000001": RETURNS_A, "000002": RETURNS_A, "000003": RETURNS_A},
            change_rates={"000001": 10.0, "000002": 10.0, "000003": 10.0},
        )

        # When
        clusters = find_clusters(panel, _target_date(panel), DEFAULT_PARAMS)

        # Then
        assert clusters == ()

    def test_same_data_forms_one_cluster_without_residual(self):
        """
        목적: 잔차 제거를 끄면 같은 데이터가 하나의 큰 클러스터가 된다 — 제거 장치의 효과를 대조한다.

        Given: 위와 동일한 데이터
        When: use_residual_correlation=False 로 find_clusters 호출
        Then: 세 종목이 한 클러스터로 묶인다
        """
        # Given
        panel = _panel(
            {"000001": RETURNS_A, "000002": RETURNS_A, "000003": RETURNS_A},
            change_rates={"000001": 10.0, "000002": 10.0, "000003": 10.0},
        )
        params = StrategyParams(
            correlation_window=6,
            correlation_threshold=0.5,
            co_move_rate=0.05,
            min_cluster_size=2,
            use_residual_correlation=False,
            min_market_cap=0,
            min_trading_value=0,
            min_listed_days=0,
        )

        # When
        clusters = find_clusters(panel, _target_date(panel), params)

        # Then
        assert len(clusters) == 1
        assert len(clusters[0].tickers) == 3


class TestUniverseGates:
    """유니버스 게이트를 고정한다."""

    def test_halted_ticker_is_excluded(self):
        """
        목적: 거래정지 종목은 후보가 될 수 없다.

        Given: 동반 급등한 두 종목 중 하나가 신호일에 거래정지
        When: find_clusters 호출
        Then: 남은 한 종목만으로는 클러스터가 서지 않는다
        """
        # Given
        panel = _panel(
            {"000001": RETURNS_A, "000002": RETURNS_A, "000003": RETURNS_C},
            change_rates={"000001": 10.0, "000002": 10.0, "000003": 1.0},
            flags={"000002": {COL_IS_HALTED: True}},
        )

        # When / Then
        assert find_clusters(panel, _target_date(panel), DEFAULT_PARAMS) == ()

    def test_shares_jump_ticker_is_excluded(self):
        """
        목적: 상장주식수 급변일은 등락률이 왜곡돼 급등으로 오인된다 — 후보에서 뺀다.

        Given: 동반 급등한 두 종목 중 하나가 액션일
        When: find_clusters 호출
        Then: 클러스터가 서지 않는다
        """
        # Given
        panel = _panel(
            {"000001": RETURNS_A, "000002": RETURNS_A, "000003": RETURNS_C},
            change_rates={"000001": 10.0, "000002": 10.0, "000003": 1.0},
            flags={"000002": {COL_IS_SHARES_JUMP: True}},
        )

        # When / Then
        assert find_clusters(panel, _target_date(panel), DEFAULT_PARAMS) == ()

    def test_market_cap_floor_excludes_small_caps(self):
        """
        목적: 시총 하한으로 초소형주를 배제할 수 있다.

        Given: 동반 급등한 두 종목 중 하나가 시총 하한 미만
        When: find_clusters 호출
        Then: 클러스터가 서지 않는다
        """
        # Given
        panel = _panel(
            {"000001": RETURNS_A, "000002": RETURNS_A, "000003": RETURNS_C},
            change_rates={"000001": 10.0, "000002": 10.0, "000003": 1.0},
            market_caps={"000002": 1_000_000_000},
        )
        params = StrategyParams(
            correlation_window=6,
            correlation_threshold=0.5,
            co_move_rate=0.05,
            min_cluster_size=2,
            min_market_cap=30_000_000_000,
            min_trading_value=0,
            min_listed_days=0,
        )

        # When / Then
        assert find_clusters(panel, _target_date(panel), params) == ()


class TestStrengthScore:
    """테마 강도 점수의 계약을 고정한다."""

    def test_clusters_are_sorted_by_strength(self):
        """
        목적: 클러스터는 **강도 점수 내림차순**으로 나온다 — 하루에 한 종목만 사므로 순서가 곧 선택이다.

        Given: 같은 날 두 테마가 서고 한쪽이 거래대금·등락률 모두 우위
        When: find_clusters 호출
        Then: 우위인 테마가 첫 번째이고 점수도 더 높다
        """
        # Given
        panel = _panel(
            {"000001": RETURNS_A, "000002": RETURNS_A, "000003": RETURNS_D, "000004": RETURNS_D},
            change_rates={"000001": 20.0, "000002": 20.0, "000003": 6.0, "000004": 6.0},
            values={
                "000001": 9_000_000_000,
                "000002": 8_000_000_000,
                "000003": 2_000_000_000,
                "000004": 1_000_000_000,
            },
        )

        # When
        clusters = find_clusters(panel, _target_date(panel), DEFAULT_PARAMS)

        # Then
        assert len(clusters) == 2
        assert set(clusters[0].tickers) == {"000001", "000002"}
        assert clusters[0].strength_score > clusters[1].strength_score

    def test_score_stays_within_unit_range(self):
        """
        목적: 점수는 0~1이다 — 다른 날에 잡힌 테마끼리 비교하려면 그날의 클러스터 수에 의존하면 안 된다.

        Given: 같은 날 두 테마가 서는 패널
        When: find_clusters 호출
        Then: 모든 점수가 0 이상 1 이하다
        """
        # Given
        panel = _panel(
            {"000001": RETURNS_A, "000002": RETURNS_A, "000003": RETURNS_D, "000004": RETURNS_D},
            change_rates={"000001": 20.0, "000002": 20.0, "000003": 6.0, "000004": 6.0},
            values={
                "000001": 9_000_000_000,
                "000002": 8_000_000_000,
                "000003": 2_000_000_000,
                "000004": 1_000_000_000,
            },
        )

        # When
        clusters = find_clusters(panel, _target_date(panel), DEFAULT_PARAMS)

        # Then
        assert all(0.0 <= cluster.strength_score <= 1.0 for cluster in clusters)

    def test_lone_cluster_gets_full_score(self):
        """
        목적: 비교 대상이 없는 날의 유일한 테마는 만점을 받는다 — 그날 시장에서 가장 강한 테마이기 때문이다.

        Given: 클러스터가 하나만 서는 날
        When: find_clusters 호출
        Then: 점수가 1.0이다
        """
        # Given
        panel = _panel(
            {"000001": RETURNS_A, "000002": RETURNS_A, "000003": RETURNS_C},
            change_rates={"000001": 10.0, "000002": 10.0, "000003": 1.0},
        )

        # When
        clusters = find_clusters(panel, _target_date(panel), DEFAULT_PARAMS)

        # Then
        assert len(clusters) == 1
        assert clusters[0].strength_score == pytest.approx(1.0)

    def test_top_k_caps_the_value_sum(self):
        """
        목적: 거래대금 합은 **상위 K종목까지만** 센다 — 잡주가 여럿 붙었다고 테마가 강해지면 안 된다.

        Given: 3종목 클러스터와 상위 2종목만 합산하는 파라미터
        When: find_clusters 호출
        Then: 합계가 가장 큰 두 종목의 합이며 세 번째는 빠진다
        """
        # Given
        panel = _panel(
            {"000001": RETURNS_A, "000002": RETURNS_A, "000003": RETURNS_A, "000004": RETURNS_C},
            change_rates={"000001": 10.0, "000002": 10.0, "000003": 10.0, "000004": 1.0},
            values={
                "000001": 9_000_000_000,
                "000002": 8_000_000_000,
                "000003": 5_000_000_000,
                "000004": 1_000_000_000,
            },
        )
        params = StrategyParams(
            correlation_window=6,
            correlation_threshold=0.5,
            co_move_rate=0.05,
            min_cluster_size=2,
            strength_top_k=2,
            min_market_cap=0,
            min_trading_value=0,
            min_listed_days=0,
        )

        # When
        clusters = find_clusters(panel, _target_date(panel), params)

        # Then
        assert len(clusters[0].tickers) == 3
        assert clusters[0].top_value_sum == 17_000_000_000

    def test_cluster_size_floor_drops_narrow_theme(self):
        """
        목적: 구성 종목이 하한에 못 미치는 테마는 후보에서 빠진다 — "테마가 너무 적은 것"을 피한다.

        Given: 2종목 테마만 서는 날과 하한 3
        When: find_clusters 호출
        Then: 클러스터가 없다
        """
        # Given
        panel = _panel(
            {"000001": RETURNS_A, "000002": RETURNS_A, "000003": RETURNS_C},
            change_rates={"000001": 10.0, "000002": 10.0, "000003": 1.0},
        )
        params = StrategyParams(
            correlation_window=6,
            correlation_threshold=0.5,
            co_move_rate=0.05,
            min_cluster_size=3,
            min_market_cap=0,
            min_trading_value=0,
            min_listed_days=0,
        )

        # When / Then
        assert find_clusters(panel, _target_date(panel), params) == ()

    def test_metrics_are_reported_in_expected_units(self):
        """
        목적: 강도 척도의 단위를 고정한다 — 거래대금은 원(정수), 평균 등락률은 비율(0.10 = 10%)이다.

        Given: 등락률 10%, 대장주 거래대금 9,000,000,000원인 2종목 테마
        When: find_clusters 호출
        Then: 대장주 거래대금은 원 그대로, 평균 등락률은 비율로 나온다
        """
        # Given
        panel = _panel(
            {"000001": RETURNS_A, "000002": RETURNS_A, "000003": RETURNS_C},
            change_rates={"000001": 10.0, "000002": 10.0, "000003": 1.0},
            values={"000001": 9_000_000_000, "000002": 8_000_000_000},
        )

        # When
        cluster = find_clusters(panel, _target_date(panel), DEFAULT_PARAMS)[0]

        # Then
        assert cluster.leader_value == 9_000_000_000
        assert cluster.mean_change_rate == pytest.approx(0.10)


class TestLookAhead:
    """신호일 이후 데이터가 결과에 영향을 주지 않음을 고정한다."""

    def test_future_rows_do_not_change_signal(self):
        """
        목적: **look-ahead 감시** — 신호일 이후 구간을 붙여도 그날의 클러스터가 달라지면 안 된다.

        Given: 신호일까지의 패널과, 그 뒤에 미래 구간이 덧붙은 패널
        When: 같은 신호일로 find_clusters 호출
        Then: 두 결과가 동일하다
        """
        # Given — 신호일 다음 날 A와 B가 정반대로 움직여 관계가 뒤집히게 만든다
        signal_position = len(RETURNS_A)
        extended = _panel(
            {
                "000001": [*RETURNS_A, -0.50],
                "000002": [*RETURNS_A, 4.00],
                "000003": [*RETURNS_C, 0.00],
            },
            change_rates={"000001": 10.0, "000002": 10.0, "000003": 1.0},
            surge_position=signal_position,
        )
        target = START_DATE + timedelta(days=signal_position)
        past = extended[extended[COL_DATE] <= pd.Timestamp(target)]

        # When
        before = find_clusters(past, target, DEFAULT_PARAMS)
        after = find_clusters(extended, target, DEFAULT_PARAMS)

        # Then
        assert before == after
        assert len(before) == 1
