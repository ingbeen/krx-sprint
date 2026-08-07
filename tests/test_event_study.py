"""event_study 테스트

신호 예측력 측정의 정의를 고정한다. **정의가 곧 결론을 만드는** 계층이라 산식을 손계산으로 박는다.

핵심 계약은 네 가지다.
- 수익률은 **수정주가**로 계산하고, 신호일 종가 기준과 익일 시가 기준을 나란히 낸다
- 초과수익은 **같은 날 동일가중 평균** 대비다. 시장이 통째로 오른 날 전 종목이 알파를 갖지 않게 한다
- **폐지는 마지막 종가까지 자르고, 패널 끝 경계는 무효**로 둔다. 전자를 빼면 생존편향,
  후자를 자르면 20거래일 수익률이 며칠짜리로 측정된다
- 창 안에 수정 미반영 액션이 있거나 가격이 0 이하면 무효다
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from krx_sprint.backtest.engine import run_backtest
from krx_sprint.backtest.event_study import (
    ReturnBasis,
    SignalLayer,
    add_excess_returns,
    compute_forward_returns,
    excess_column,
    extract_signal_layers,
    return_column,
    summarize_layers,
)
from krx_sprint.common_constants import (
    COL_ADJ_CLOSE,
    COL_ADJ_OPEN,
    COL_DATE,
    COL_IS_UNADJUSTED_ACTION,
    COL_TICKER,
)

START_DATE = date(2024, 6, 3)


def _panel(
    closes: dict[str, list[float]],
    opens: dict[str, list[float]] | None = None,
    actions: dict[str, list[bool]] | None = None,
    lengths: dict[str, int] | None = None,
) -> pd.DataFrame:
    """이벤트 스터디에 필요한 최소 패널을 만든다.

    `lengths`로 종목별 관측 길이를 줄이면 그 종목은 그 날짜에 폐지된 것으로 취급된다.
    시가는 생략하면 종가와 같다.
    """
    rows: list[dict[str, object]] = []

    for ticker, series in closes.items():
        limit = (lengths or {}).get(ticker, len(series))
        for index in range(limit):
            rows.append(
                {
                    COL_DATE: pd.Timestamp(START_DATE + timedelta(days=index)),
                    COL_TICKER: ticker,
                    COL_ADJ_OPEN: (opens or {}).get(ticker, series)[index],
                    COL_ADJ_CLOSE: series[index],
                    COL_IS_UNADJUSTED_ACTION: (actions or {}).get(ticker, [False] * len(series))[index],
                }
            )

    return pd.DataFrame(rows).sort_values([COL_DATE, COL_TICKER]).reset_index(drop=True)


def _value(frame: pd.DataFrame, ticker: str, day: int, column: str) -> float:
    """특정 (티커, 일자)의 값을 꺼낸다."""
    target = pd.Timestamp(START_DATE + timedelta(days=day))
    row = frame[(frame[COL_TICKER] == ticker) & (frame[COL_DATE] == target)]

    return float(row[column].iloc[0])


class TestForwardReturns:
    """forward return 산식을 고정한다."""

    def test_close_basis_matches_hand_calculation(self):
        """
        목적: 신호일 종가 기준 수익률은 **D+k 종가 ÷ 신호일 종가 − 1**이다.

        Given: 종가 100 · 110 · 121 인 종목
        When: 보유 구간 1·2로 계산
        Then: 첫날 기준 1일 +10%, 2일 +21%
        """
        # Given
        panel = _panel({"000001": [100.0, 110.0, 121.0], "000002": [100.0, 100.0, 100.0]})

        # When
        frame = compute_forward_returns(panel, horizons=(1, 2))

        # Then
        assert _value(frame, "000001", 0, return_column(ReturnBasis.CLOSE, 1)) == pytest.approx(0.10)
        assert _value(frame, "000001", 0, return_column(ReturnBasis.CLOSE, 2)) == pytest.approx(0.21)

    def test_next_open_basis_starts_from_the_following_open(self):
        """
        목적: 익일 시가 기준 수익률은 **D+k 종가 ÷ D+1 시가 − 1**이다 — 예약매매로 잡을 수 있는 구간이다.

        Given: 신호일 종가 100, 다음 날 시가 105, 다음 날 종가 110
        When: 보유 구간 1로 계산
        Then: 110/105 − 1
        """
        # Given
        panel = _panel(
            {"000001": [100.0, 110.0, 121.0], "000002": [100.0, 100.0, 100.0]},
            opens={"000001": [100.0, 105.0, 115.0], "000002": [100.0, 100.0, 100.0]},
        )

        # When
        frame = compute_forward_returns(panel, horizons=(1,))

        # Then
        assert _value(frame, "000001", 0, return_column(ReturnBasis.NEXT_OPEN, 1)) == pytest.approx(110 / 105 - 1)

    def test_gap_shows_up_as_a_gap_between_the_two_bases(self):
        """
        목적: 두 기준의 차이가 **갭으로 새는 몫**이다 — 이 차이를 보려고 둘을 함께 낸다.

        Given: 신호일 종가보다 훨씬 높게 갭 상승해 시작한 다음 날
        When: 두 기준으로 계산
        Then: 익일 시가 기준이 종가 기준보다 낮다
        """
        # Given
        panel = _panel(
            {"000001": [100.0, 120.0], "000002": [100.0, 100.0]},
            opens={"000001": [100.0, 118.0], "000002": [100.0, 100.0]},
        )

        # When
        frame = compute_forward_returns(panel, horizons=(1,))

        # Then
        assert _value(frame, "000001", 0, return_column(ReturnBasis.NEXT_OPEN, 1)) < _value(
            frame, "000001", 0, return_column(ReturnBasis.CLOSE, 1)
        )

    def test_rejects_non_positive_horizon(self):
        """
        목적: 보유 구간이 0 이하면 측정이 성립하지 않는다.

        Given: 보유 구간 0
        When: compute_forward_returns 호출
        Then: ValueError
        """
        panel = _panel({"000001": [100.0, 110.0]})

        with pytest.raises(ValueError, match="보유 구간"):
            compute_forward_returns(panel, horizons=(0,))


class TestBoundaryHandling:
    """폐지와 구간 끝 처리를 고정한다 — 생존편향이 들어오는 지점이다."""

    def test_delisted_ticker_is_truncated_to_its_last_close(self):
        """
        목적: 폐지 종목은 **마지막 종가까지 자른다**. 표본에서 빼면 폐지 종목의 손실이 사라진다.

        Given: 3일차에 사라지는 종목(마지막 종가 50)과 끝까지 남는 종목
        When: 보유 구간 3으로 계산
        Then: 폐지 종목의 첫날 수익률이 마지막 종가로 계산되고 절단 표시가 붙는다
        """
        # Given
        panel = _panel(
            {"000001": [100.0, 80.0, 50.0, 0.0, 0.0], "000002": [100.0] * 5},
            lengths={"000001": 3},
        )

        # When
        frame = compute_forward_returns(panel, horizons=(3,))

        # Then
        assert _value(frame, "000001", 0, return_column(ReturnBasis.CLOSE, 3)) == pytest.approx(-0.50)
        assert _value(frame, "000001", 0, "truncated_3") == 1.0

    def test_panel_boundary_is_invalid_not_truncated(self):
        """
        목적: 상장이 이어지는데 데이터만 끝난 구간은 **무효**다 — 자르면 3거래일 수익률이 1일로 측정된다.

        Given: 끝까지 살아 있는 종목
        When: 마지막 근처에서 보유 구간 3으로 계산
        Then: 값이 없다(NaN)
        """
        # Given
        panel = _panel({"000001": [100.0] * 4, "000002": [100.0] * 4})

        # When
        frame = compute_forward_returns(panel, horizons=(3,))

        # Then
        assert pd.isna(_value(frame, "000001", 2, return_column(ReturnBasis.CLOSE, 3)))


class TestExclusionRules:
    """무효 처리 규칙을 고정한다 (백테스트 설계 v1 §3)."""

    def test_unadjusted_action_inside_the_window_invalidates(self):
        """
        목적: 창 안에 **수정 미반영 액션**이 있으면 가짜 갭이라 수익률을 믿을 수 없다.

        Given: 2일차에 수정 미반영 액션이 있는 종목
        When: 그 액션을 창에 포함하는 구간과 포함하지 않는 구간으로 계산
        Then: 포함하는 쪽만 무효다
        """
        # Given
        panel = _panel(
            {"000001": [100.0, 110.0, 300.0, 310.0], "000002": [100.0] * 4},
            actions={"000001": [False, False, True, False], "000002": [False] * 4},
        )

        # When
        frame = compute_forward_returns(panel, horizons=(1, 2))

        # Then
        assert _value(frame, "000001", 0, return_column(ReturnBasis.CLOSE, 1)) == pytest.approx(0.10)
        assert pd.isna(_value(frame, "000001", 0, return_column(ReturnBasis.CLOSE, 2)))

    def test_halted_price_invalidates_the_observation(self):
        """
        목적: 종가가 0 이하(거래정지)면 수익률이 성립하지 않는다.

        Given: 종료 시점 종가가 0인 종목
        When: 그 시점을 끝으로 하는 구간으로 계산
        Then: 값이 없다
        """
        # Given
        panel = _panel({"000001": [100.0, 0.0, 120.0], "000002": [100.0] * 3})

        # When
        frame = compute_forward_returns(panel, horizons=(1,))

        # Then
        assert pd.isna(_value(frame, "000001", 0, return_column(ReturnBasis.CLOSE, 1)))


class TestExcessReturns:
    """초과수익 정의를 고정한다."""

    def test_excess_is_measured_against_the_same_day_average(self):
        """
        목적: 초과수익은 **같은 날 동일가중 평균** 대비다.

        Given: 같은 날 +20%·+10%·0% 인 세 종목 (평균 +10%)
        When: 초과수익 계산
        Then: 각각 +10%p·0%p·−10%p
        """
        # Given
        panel = _panel(
            {
                "000001": [100.0, 120.0, 120.0],
                "000002": [100.0, 110.0, 110.0],
                "000003": [100.0, 100.0, 100.0],
            }
        )

        # When
        frame = add_excess_returns(compute_forward_returns(panel, horizons=(1,)), horizons=(1,))

        # Then
        column = excess_column(ReturnBasis.CLOSE, 1)
        assert _value(frame, "000001", 0, column) == pytest.approx(0.10)
        assert _value(frame, "000002", 0, column) == pytest.approx(0.0)
        assert _value(frame, "000003", 0, column) == pytest.approx(-0.10)

    def test_market_wide_move_leaves_no_excess(self):
        """
        목적: 시장이 통째로 오른 날 모든 종목이 알파를 가진 것처럼 보이면 안 된다.

        Given: 모든 종목이 똑같이 +20% 오른 날
        When: 초과수익 계산
        Then: 전부 0이다
        """
        # Given
        panel = _panel({ticker: [100.0, 120.0] for ticker in ("000001", "000002", "000003")})

        # When
        frame = add_excess_returns(compute_forward_returns(panel, horizons=(1,)), horizons=(1,))

        # Then
        column = excess_column(ReturnBasis.CLOSE, 1)
        assert frame[column].dropna().abs().max() == pytest.approx(0.0)

    def test_baseline_population_can_be_restricted(self):
        """
        목적: 기준선 모집단을 좁힐 수 있다 — 신호 종목이 그 부분집합이어야 비교가 공정하다.

        Given: 세 종목 중 두 종목만 기준선에 넣는 마스크
        When: 초과수익 계산
        Then: 기준선이 그 두 종목의 평균(+15%)으로 잡힌다
        """
        # Given
        panel = _panel(
            {
                "000001": [100.0, 120.0],
                "000002": [100.0, 110.0],
                "000003": [100.0, 100.0],
            }
        )
        frame = compute_forward_returns(panel, horizons=(1,))
        mask = frame[COL_TICKER].isin(["000001", "000002"])

        # When
        result = add_excess_returns(frame, horizons=(1,), baseline_mask=mask)

        # Then
        assert _value(result, "000001", 0, excess_column(ReturnBasis.CLOSE, 1)) == pytest.approx(0.05)

    def test_rejects_mask_of_wrong_length(self):
        """
        목적: 길이가 맞지 않는 마스크는 조용히 어긋난 기준선을 만든다 — 즉시 거부한다.

        Given: 프레임보다 짧은 마스크
        When: add_excess_returns 호출
        Then: ValueError
        """
        # Given
        panel = _panel({"000001": [100.0, 120.0], "000002": [100.0, 110.0]})
        frame = compute_forward_returns(panel, horizons=(1,))

        # When / Then
        with pytest.raises(ValueError, match="마스크 길이"):
            add_excess_returns(frame, horizons=(1,), baseline_mask=pd.Series([True]))


class TestSignalLayers:
    """신호 계층 추출의 계약을 고정한다."""

    def test_layers_match_the_engine_order_dates(self):
        """
        목적: **계약 테스트** — 최종 계층(정배열)이 엔진이 실제로 주문을 내는 (일자, 종목)과 일치한다.
        두 경로가 갈라지면 측정 대상이 실제 전략과 다른 것이 된다.

        Given: 엔진 테스트와 같은 밴드 진입 시나리오
        When: 이벤트 스터디의 계층 추출과 엔진 실행을 각각 돌린다
        Then: 정배열 계층이 표시된 일자에 엔진도 진입 후보를 만든다
        """
        # Given
        import test_engine as engine_fixtures

        closes = engine_fixtures._band_closes([1_095, 1_300, 1_300])
        panel = engine_fixtures._panel(closes, engine_fixtures._band_values(len(closes[engine_fixtures.LEADER])))
        params = engine_fixtures.BAND_PARAMS

        # When
        layers = extract_signal_layers(panel, params)
        result = run_backtest(panel, engine_fixtures._trading_days(40), params, engine_fixtures.EXECUTION)

        # Then
        assert layers["aligned"].sum() > 0
        assert result.diagnostics.order_count == int(layers["aligned"].sum())

    def test_layers_are_nested_within_the_surge_day_group(self):
        """
        목적: 급등일 그룹의 층은 앞 층의 부분집합이다 — 조건을 더하는 구조라야 분해가 성립한다.

        Given: 밴드 진입 시나리오
        When: 계층 추출
        Then: 대장주 ⊆ 테마 소속 ⊆ 급등 ⊆ 유니버스
        """
        # Given
        import test_engine as engine_fixtures

        closes = engine_fixtures._band_closes([1_095, 1_300, 1_300])
        panel = engine_fixtures._panel(closes, engine_fixtures._band_values(len(closes[engine_fixtures.LEADER])))

        # When
        layers = extract_signal_layers(panel, engine_fixtures.BAND_PARAMS)

        # Then
        assert not (layers["leader"] & ~layers["clustered"]).any()
        assert not (layers["clustered"] & ~layers["surged"]).any()
        assert not (layers["surged"] & ~layers["universe"]).any()

    def test_future_rows_do_not_change_the_layers(self):
        """
        목적: **look-ahead 감시** — 신호일 뒤에 구간이 더 붙어도 그날의 계층 판정이 달라지면 안 된다.

        Given: 같은 시나리오의 짧은 패널과 뒤가 더 붙은 패널
        When: 각각 계층 추출
        Then: 겹치는 구간의 판정이 동일하다
        """
        # Given
        import test_engine as engine_fixtures

        short_closes = engine_fixtures._band_closes([1_095])
        long_closes = engine_fixtures._band_closes([1_095, 1_300, 1_300])
        short_panel = engine_fixtures._panel(
            short_closes, engine_fixtures._band_values(len(short_closes[engine_fixtures.LEADER]))
        )
        long_panel = engine_fixtures._panel(
            long_closes, engine_fixtures._band_values(len(long_closes[engine_fixtures.LEADER]))
        )

        # When
        short_layers = extract_signal_layers(short_panel, engine_fixtures.BAND_PARAMS)
        long_layers = extract_signal_layers(long_panel, engine_fixtures.BAND_PARAMS)

        # Then
        overlap = long_layers[long_layers[COL_DATE].isin(short_layers[COL_DATE])].reset_index(drop=True)
        pd.testing.assert_frame_equal(short_layers, overlap)


class TestSummarizeLayers:
    """집계의 표본 단위 계약을 고정한다."""

    def test_day_unit_is_the_default_test_sample(self):
        """
        목적: 같은 날 여러 종목이 있어도 **일자 단위로 먼저 평균**을 낸다 — 같은 날 종목은 독립이 아니다.

        Given: 하루에 세 종목이 모두 계층에 속하고 이틀치가 있는 프레임
        When: summarize_layers 호출
        Then: 일자 표본 수가 2이고 종목 표본 수가 6이다
        """
        # Given
        panel = _panel({f"00000{index}": [100.0, 110.0, 120.0] for index in range(1, 4)})
        frame = add_excess_returns(compute_forward_returns(panel, horizons=(1,)), horizons=(1,))
        frame[SignalLayer.UNIVERSE.name.lower()] = True

        # When
        summary = summarize_layers(frame, layers=(SignalLayer.UNIVERSE,), horizons=(1,))

        # Then
        row = summary[summary["basis"] == ReturnBasis.CLOSE.value].iloc[0]
        assert int(row["day_count"]) == 2
        assert int(row["ticker_count"]) == 6

    def test_rejects_missing_layer_column(self):
        """
        목적: 계층 컬럼이 없으면 조용히 빈 집계를 내지 않고 즉시 거부한다.

        Given: 계층 컬럼이 없는 프레임
        When: summarize_layers 호출
        Then: ValueError
        """
        # Given
        panel = _panel({"000001": [100.0, 110.0], "000002": [100.0, 100.0]})
        frame = add_excess_returns(compute_forward_returns(panel, horizons=(1,)), horizons=(1,))

        # When / Then
        with pytest.raises(ValueError, match="계층 컬럼"):
            summarize_layers(frame, layers=(SignalLayer.UNIVERSE,), horizons=(1,))
