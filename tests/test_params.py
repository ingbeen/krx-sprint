"""params 테스트

전략 파라미터의 유효성 검증을 고정한다 (백테스트 설계 §10.1).

파라미터는 백테스트의 자유도 그 자체다. 잘못된 값이 조용히 통과하면 결과 해석이 통째로 무너지므로
생성 시점에 즉시 거부한다.
"""

import pytest

from krx_sprint.backtest.params import ExecutionParams, StrategyParams


class TestStrategyParams:
    """전략 파라미터 검증을 고정한다."""

    def test_defaults_are_valid(self):
        """
        목적: 기본값만으로 생성이 가능해야 한다 (기본 실행의 전제).

        Given: 인자 없음
        When: StrategyParams 생성
        Then: 예외 없이 만들어진다
        """
        assert StrategyParams().max_positions >= 1

    @pytest.mark.parametrize(
        "field,value",
        [
            ("correlation_window", 0),
            ("value_average_window", 0),
            ("base_bar_expiry_days", -1),
            ("entry_ma_period", 0),
            ("stop_loss_ma_period", 0),
        ],
    )
    def test_rejects_non_positive_windows(self, field, value):
        """
        목적: 창 길이가 0 이하면 지표를 정의할 수 없다.

        Given: 0 이하 창 길이
        When: StrategyParams 생성
        Then: ValueError
        """
        with pytest.raises(ValueError, match=field):
            StrategyParams(**{field: value})

    @pytest.mark.parametrize(
        "field,value",
        [
            ("co_move_rate", 1.5),
            ("base_bar_rise_rate", -0.1),
            ("entry_discount_rate", 2.0),
            ("stop_loss_rate", 0.0),
            ("swing_reversal_rate", 0.0),
        ],
    )
    def test_rejects_out_of_range_rates(self, field, value):
        """
        목적: 비율은 0~1 소수여야 한다 (루트 CLAUDE.md 비율 표기 규칙).

        Given: 범위를 벗어난 비율
        When: StrategyParams 생성
        Then: ValueError
        """
        with pytest.raises(ValueError, match=field):
            StrategyParams(**{field: value})

    def test_rejects_single_ticker_cluster_multiple(self):
        """
        목적: 거래대금 배수가 1 이하면 "급증"이 성립하지 않는다.

        Given: 배수 1.0
        When: StrategyParams 생성
        Then: ValueError
        """
        with pytest.raises(ValueError, match="value_surge_multiple"):
            StrategyParams(value_surge_multiple=1.0)

    def test_rejects_zero_decline_limit(self):
        """
        목적: 최대 하락 차수가 0이면 어떤 진입도 불가능하다.

        Given: max_decline_count=0
        When: StrategyParams 생성
        Then: ValueError
        """
        with pytest.raises(ValueError, match="max_decline_count"):
            StrategyParams(max_decline_count=0)

    def test_rejects_non_positive_equity(self):
        """
        목적: 초기 자본이 0 이하면 백테스트가 성립하지 않는다.

        Given: 초기 자본 0
        When: StrategyParams 생성
        Then: ValueError
        """
        with pytest.raises(ValueError, match="initial_equity"):
            StrategyParams(initial_equity=0)


class TestExecutionParams:
    """체결 가정 검증을 고정한다."""

    def test_defaults_are_valid(self):
        """
        목적: 기본 가정만으로 생성이 가능해야 한다.

        Given: 인자 없음
        When: ExecutionParams 생성
        Then: 거래세가 켜져 있다
        """
        assert ExecutionParams().include_tax is True

    def test_rejects_negative_fee_rate(self):
        """
        목적: 음수 수수료는 비용이 아니라 수익이 된다.

        Given: 음수 요율
        When: ExecutionParams 생성
        Then: ValueError
        """
        with pytest.raises(ValueError, match="fee_rate"):
            ExecutionParams(fee_rate=-0.001)

    def test_rejects_zero_liquidity_participation(self):
        """
        목적: 참여율 0이면 어떤 주문도 체결될 수 없다.

        Given: 참여율 0
        When: ExecutionParams 생성
        Then: ValueError
        """
        with pytest.raises(ValueError, match="liquidity_participation_rate"):
            ExecutionParams(liquidity_participation_rate=0.0)

    def test_rejects_out_of_range_delisting_penalty(self):
        """
        목적: 폐지 페널티는 0~1 비율이다.

        Given: 1을 넘는 페널티
        When: ExecutionParams 생성
        Then: ValueError
        """
        with pytest.raises(ValueError, match="delisting_penalty_rate"):
            ExecutionParams(delisting_penalty_rate=1.5)

    def test_tax_can_be_disabled_for_sanity_check(self):
        """
        목적: 무비용 대조군을 위해 거래세를 끌 수 있다 (설계 §10.5).

        Given: include_tax=False
        When: ExecutionParams 생성
        Then: 값이 그대로 유지된다
        """
        assert ExecutionParams(include_tax=False).include_tax is False
