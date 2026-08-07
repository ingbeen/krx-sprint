"""신호 예측력 이벤트 스터디 — forward return과 초과수익

매매 규칙 없이 **신호가 미래 수익률을 예측하는지만** 측정하기 위한 계층이다.
진입가·손절·익절·자금배분·비용을 넣지 않는다. 규칙을 섞으면 성과가 나빠도 신호 탓인지
규칙 탓인지 분리할 수 없고, 체결된 트레이드만 남아 표본이 수십 건으로 줄어든다.

두 가지 기준으로 수익률을 낸다.

- **신호일 종가 기준** — 신호가 가진 순수한 예측력
- **익일 시가 기준** — 예약매매로 실제 잡을 수 있는 구간. 두 값의 차이가 곧 갭으로 새는 몫이다

수익률은 **2단 수정주가**로 계산한다. 원본가로 계산하면 분할 구간에서 깨진다 (백테스트 설계 v1 §3.1).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

import numpy as np
import pandas as pd

from krx_sprint.backtest.params import StrategyParams
from krx_sprint.backtest.signals import band_split_prices, count_declines, mark_base_bars, next_day_moving_average
from krx_sprint.backtest.theme import apply_universe_gates, find_clusters, surged_tickers
from krx_sprint.common_constants import (
    COL_ADJ_CLOSE,
    COL_ADJ_OPEN,
    COL_DATE,
    COL_IS_UNADJUSTED_ACTION,
    COL_TICKER,
)

# 측정할 보유 구간 (거래일). 짧은 반등부터 한 달 스윙까지 훑는다
DEFAULT_HORIZONS = (1, 3, 5, 10, 20)

# 파생 컬럼 접두사
COL_TRUNCATED_PREFIX = "truncated_"


class ReturnBasis(Enum):
    """수익률 시작점

    Attributes:
        CLOSE: 신호일 종가에서 시작 — 신호의 순수 예측력
        NEXT_OPEN: 다음 거래일 시가에서 시작 — 예약매매로 실제 잡을 수 있는 구간
    """

    CLOSE = "종가"
    NEXT_OPEN = "익일시가"


def return_column(basis: ReturnBasis, horizon: int) -> str:
    """forward return 컬럼명을 만든다.

    Args:
        basis: 수익률 시작점
        horizon: 보유 구간 (거래일)

    Returns:
        컬럼명
    """
    return f"fwd_{basis.name.lower()}_{horizon}"


def excess_column(basis: ReturnBasis, horizon: int) -> str:
    """초과수익 컬럼명을 만든다.

    Args:
        basis: 수익률 시작점
        horizon: 보유 구간 (거래일)

    Returns:
        컬럼명
    """
    return f"excess_{basis.name.lower()}_{horizon}"


def compute_forward_returns(
    panel: pd.DataFrame,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
) -> pd.DataFrame:
    """(일자, 티커)별 forward return을 구한다.

    **폐지 종목은 마지막 종가까지로 자른다.** 표본에서 빼면 폐지 종목의 손실이 사라져
    생존편향이 생긴다. 반대로 **패널 마지막 일자에 걸린 구간은 무효**로 둔다 — 그 종목은 여전히
    상장돼 있고 우리에게 데이터가 없을 뿐이라, 자르면 20거래일 수익률이 2거래일로 측정된다.

    무효로 처리하는 경우는 세 가지다.
    시작·종료 종가가 0 이하(거래정지) / 창 안에 **수정 미반영 액션**이 있음 / 위의 구간 끝 초과.

    Args:
        panel: 통합 패널 ((일자, 티커) 오름차순)
        horizons: 보유 구간 목록 (거래일, 모두 1 이상)

    Returns:
        일자·티커·수익률·절단 여부 컬럼을 담은 프레임 (원본 패널은 변경하지 않는다)

    Raises:
        ValueError: 패널이 비었거나 보유 구간이 1 미만인 경우
    """
    if panel.empty:
        raise ValueError("패널이 비어 있습니다")

    invalid = [horizon for horizon in horizons if horizon < 1]
    if invalid:
        raise ValueError(f"보유 구간은 1 이상이어야 합니다: {invalid}")

    frame = panel[[COL_DATE, COL_TICKER, COL_ADJ_OPEN, COL_ADJ_CLOSE, COL_IS_UNADJUSTED_ACTION]].copy()
    grouped = frame.groupby(COL_TICKER, observed=True)

    # 1. 종목별 마지막 관측과 폐지 여부 — 폐지만 절단 대상이다
    last_close = grouped[COL_ADJ_CLOSE].transform("last")
    is_delisted = grouped[COL_DATE].transform("max") < frame[COL_DATE].max()

    # 2. 수정 미반영 액션의 누적 개수. 창 양끝의 차이가 0보다 크면 창 안에 액션이 있다
    frame["_action_count"] = grouped[COL_IS_UNADJUSTED_ACTION].cumsum()
    grouped = frame.groupby(COL_TICKER, observed=True)
    last_action_count = grouped["_action_count"].transform("last")

    next_open = grouped[COL_ADJ_OPEN].shift(-1)
    start_close = frame[COL_ADJ_CLOSE]
    start_is_sound = (start_close > 0) & ~frame[COL_IS_UNADJUSTED_ACTION]

    for horizon in horizons:
        shifted_close = grouped[COL_ADJ_CLOSE].shift(-horizon)
        truncated = shifted_close.isna() & is_delisted

        target_close = shifted_close.where(~truncated, last_close)
        target_action = grouped["_action_count"].shift(-horizon).where(~truncated, last_action_count)

        window_is_clean = (target_action - frame["_action_count"]) == 0
        usable = start_is_sound & target_close.notna() & (target_close > 0) & window_is_clean

        frame[COL_TRUNCATED_PREFIX + str(horizon)] = truncated & usable
        frame[return_column(ReturnBasis.CLOSE, horizon)] = (target_close / start_close - 1).where(usable)
        frame[return_column(ReturnBasis.NEXT_OPEN, horizon)] = (target_close / next_open - 1).where(
            usable & next_open.notna() & (next_open > 0)
        )

    return frame.drop(columns=["_action_count"])


def add_excess_returns(
    frame: pd.DataFrame,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    *,
    baseline_mask: pd.Series | None = None,
) -> pd.DataFrame:
    """같은 날 동일가중 평균을 빼 초과수익을 만든다.

    기준선을 두는 이유는 시장 전체가 오른 날 모든 종목이 알파를 가진 것처럼 보이는 것을 막기
    위해서다. 기준선 모집단은 호출자가 정한다 — 신호 종목이 그 모집단의 부분집합이어야 비교가
    공정하므로, 보통 유니버스 게이트를 통과한 행을 넘긴다.

    Args:
        frame: `compute_forward_returns` 결과
        horizons: 보유 구간 목록
        baseline_mask: 기준선 계산에 쓸 행 (생략하면 전체)

    Returns:
        초과수익 컬럼이 더해진 프레임 (입력은 변경하지 않는다)

    Raises:
        ValueError: 필요한 수익률 컬럼이 없거나 마스크 길이가 맞지 않는 경우
    """
    if baseline_mask is not None and len(baseline_mask) != len(frame):
        raise ValueError(f"기준선 마스크 길이가 프레임과 다릅니다: {len(baseline_mask)} vs {len(frame)}")

    result = frame.copy()
    population = result if baseline_mask is None else result[baseline_mask]

    for basis in ReturnBasis:
        for horizon in horizons:
            source = return_column(basis, horizon)
            if source not in result.columns:
                raise ValueError(f"수익률 컬럼이 없습니다: {source}")

            baseline = population.groupby(COL_DATE, observed=True)[source].mean()
            result[excess_column(basis, horizon)] = result[source] - result[COL_DATE].map(baseline)

    return result


@dataclass
class _TickerSeries:
    """종목별로 미리 계산해 둔 시계열

    각 시점이 과거만 보므로 한 번 계산해 재사용해도 look-ahead가 생기지 않는다.
    """

    positions: dict[date, int]
    adj_close: np.ndarray
    base_bar_positions: list[int]


class SignalLayer(Enum):
    """신호 계층

    각 층은 조건을 하나씩 더한 것이다. 층을 쌓으며 초과수익이 어떻게 변하는지가
    "어느 요소가 알파를 더하는가"의 답이 된다.

    앞의 네 층은 **급등일** 이벤트이고 뒤의 세 층은 **눌림목 진입 후보일** 이벤트다.
    이벤트 날짜의 성격이 다르므로 그룹 안에서 비교하고 그룹 사이는 비교하지 않는다.
    """

    UNIVERSE = "유니버스 게이트"
    SURGED = "당일 급등"
    CLUSTERED = "테마 소속"
    LEADER = "테마 대장주"
    BASE_BAR_ALIVE = "기준봉 유효"
    DECLINE_IN_RANGE = "하락 차수 이내"
    ALIGNED = "이동평균 정배열"


def extract_signal_layers(panel: pd.DataFrame, params: StrategyParams) -> pd.DataFrame:
    """(일자, 티커)별로 각 신호 계층 소속 여부를 판정한다.

    판정식은 전부 `theme.py`·`signals.py`의 함수를 호출해 만든다. 이 모듈이 판정식을 다시
    구현하면 백테스트 엔진과 조용히 갈라져, **측정 대상이 실제 전략과 다른 것**이 된다.

    테마를 며칠 들고 가는 흐름(급등일에 서고 유효기간 안에 진입 후보가 된다)은 엔진과 같은
    규칙이며, 두 경로가 같은 (일자, 종목)을 내는지는 계약 테스트로 고정한다.

    Args:
        panel: 통합 패널 ((일자, 티커) 오름차순)
        params: 전략 파라미터

    Returns:
        일자·티커와 계층별 bool 컬럼을 담은 프레임

    Raises:
        ValueError: 패널이 비었거나 일자 오름차순이 아닌 경우
    """
    if panel.empty:
        raise ValueError("패널이 비어 있습니다")

    if not panel[COL_DATE].is_monotonic_increasing:
        raise ValueError("패널이 일자 오름차순이 아닙니다")

    histories = _build_histories(panel, params)
    flags = {layer: np.zeros(len(panel), dtype=bool) for layer in SignalLayer}
    row_index = _row_index(panel)

    listed_days: dict[str, int] = {}
    themes: dict[str, int] = {}
    all_dates = [stamp.date() for stamp in pd.DatetimeIndex(panel[COL_DATE].unique())]

    for target in all_dates:
        window = _window_slice(panel, target, params.correlation_window)
        today = window[window[COL_DATE] == pd.Timestamp(target)]
        for ticker in today[COL_TICKER].astype(str):
            listed_days[ticker] = listed_days.get(ticker, 0) + 1

        universe = apply_universe_gates(window, today, params, listed_days)
        surged = set(surged_tickers(today, universe, params))
        clusters = find_clusters(window, target, params, listed_days=listed_days)

        members = {ticker for cluster in clusters for ticker in cluster.tickers}
        leaders = {cluster.leader for cluster in clusters}

        _mark(flags, row_index, target, universe, SignalLayer.UNIVERSE)
        _mark(flags, row_index, target, surged, SignalLayer.SURGED)
        _mark(flags, row_index, target, members, SignalLayer.CLUSTERED)
        _mark(flags, row_index, target, leaders, SignalLayer.LEADER)

        _refresh_themes(themes, leaders, histories, target, params)
        _mark_entry_layers(flags, row_index, themes, histories, target, params)

    result = panel[[COL_DATE, COL_TICKER]].copy()
    for layer, values in flags.items():
        result[layer.name.lower()] = values

    return result


def _row_index(panel: pd.DataFrame) -> dict[tuple[date, str], int]:
    """(일자, 티커) → 행 위치 사전을 만든다.

    Args:
        panel: 통합 패널

    Returns:
        위치 사전
    """
    return {
        (stamp.date(), str(ticker)): position
        for position, (stamp, ticker) in enumerate(zip(panel[COL_DATE], panel[COL_TICKER], strict=True))
    }


def _mark(
    flags: dict[SignalLayer, np.ndarray],
    row_index: dict[tuple[date, str], int],
    target: date,
    tickers: set[str],
    layer: SignalLayer,
) -> None:
    """해당 일자의 티커들에 계층 표시를 켠다.

    Args:
        flags: 계층별 bool 배열
        row_index: (일자, 티커) → 행 위치
        target: 대상 일자
        tickers: 표시할 티커
        layer: 대상 계층
    """
    for ticker in tickers:
        position = row_index.get((target, ticker))
        if position is not None:
            flags[layer][position] = True


def _build_histories(panel: pd.DataFrame, params: StrategyParams) -> dict[str, "_TickerSeries"]:
    """종목별 시계열과 기준봉 위치를 미리 계산한다.

    Args:
        panel: 통합 패널
        params: 전략 파라미터

    Returns:
        티커 → 시계열
    """
    histories: dict[str, _TickerSeries] = {}

    for ticker, group in panel.groupby(COL_TICKER, observed=True):
        frame = group.reset_index(drop=True)
        dates = [value.date() for value in frame[COL_DATE]]
        marked = mark_base_bars(frame, params)

        histories[str(ticker)] = _TickerSeries(
            positions={value: index for index, value in enumerate(dates)},
            adj_close=frame[COL_ADJ_CLOSE].to_numpy(dtype=float),
            base_bar_positions=[position for position, flag in enumerate(marked) if bool(flag)],
        )

    return histories


def _window_slice(panel: pd.DataFrame, target: date, window: int) -> pd.DataFrame:
    """상관 창 계산에 필요한 만큼만 잘라낸다.

    Args:
        panel: 통합 패널
        target: 신호일
        window: 상관 창 (거래일)

    Returns:
        신호일까지의 최근 구간
    """
    first = pd.Timestamp(target - timedelta(days=window * 3))

    return panel[(panel[COL_DATE] >= first) & (panel[COL_DATE] <= pd.Timestamp(target))]


def _refresh_themes(
    themes: dict[str, int],
    leaders: set[str],
    histories: dict[str, "_TickerSeries"],
    target: date,
    params: StrategyParams,
) -> None:
    """당일 대장주를 테마로 등록하고 유효기간이 지난 테마를 버린다.

    Args:
        themes: 살아 있는 테마 (대장주 → 기준봉 행 위치). 제자리에서 갱신된다
        leaders: 당일 클러스터의 대장주
        histories: 종목별 시계열
        target: 대상 일자
        params: 전략 파라미터
    """
    for leader in list(themes):
        history = histories.get(leader)
        position = None if history is None else history.positions.get(target)
        if position is not None and position - themes[leader] > params.base_bar_expiry_days:
            del themes[leader]

    for leader in leaders:
        history = histories.get(leader)
        position = None if history is None else history.positions.get(target)
        if history is None or position is None:
            continue

        base_position = _latest_base_bar(history, position, params)
        if base_position is not None:
            themes[leader] = base_position


def _latest_base_bar(history: "_TickerSeries", position: int, params: StrategyParams) -> int | None:
    """유효기간 안에 있는 가장 최근 기준봉 위치를 찾는다.

    Args:
        history: 종목 시계열
        position: 대상 행 위치
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


def _mark_entry_layers(
    flags: dict[SignalLayer, np.ndarray],
    row_index: dict[tuple[date, str], int],
    themes: dict[str, int],
    histories: dict[str, "_TickerSeries"],
    target: date,
    params: StrategyParams,
) -> None:
    """살아 있는 테마의 대장주에 진입 후보 계층을 표시한다.

    Args:
        flags: 계층별 bool 배열
        row_index: (일자, 티커) → 행 위치
        themes: 살아 있는 테마
        histories: 종목별 시계열
        target: 대상 일자
        params: 전략 파라미터
    """
    for leader, base_position in themes.items():
        history = histories[leader]
        position = history.positions.get(target)
        if position is None or position < base_position:
            continue

        row = row_index.get((target, leader))
        if row is None:
            continue

        flags[SignalLayer.BASE_BAR_ALIVE][row] = True

        prices = history.adj_close[: position + 1].tolist()
        if len(prices) < 2 or prices[position] <= 0:
            continue

        decline_count = count_declines(prices, base_position, params)[position]
        if not 1 <= decline_count <= params.max_decline_count:
            continue

        flags[SignalLayer.DECLINE_IN_RANGE][row] = True

        bounds = [next_day_moving_average(prices, position, period) for period in params.entry_band_periods]
        if any(bound is None for bound in bounds):
            continue

        if band_split_prices([bound for bound in bounds if bound is not None], 1):
            flags[SignalLayer.ALIGNED][row] = True


# 집계 결과 컬럼 (일자 단위가 기본 검정 대상, 종목 단위는 참고값)
SUMMARY_COLUMNS = [
    "layer",
    "basis",
    "horizon",
    "ticker_count",
    "ticker_mean_pct",
    "day_count",
    "day_mean_pct",
    "day_std_pct",
    "day_stderr_pct",
    "day_t_stat",
    "day_win_rate_pct",
]

# 백분율 변환 계수와 반올림 자릿수 (루트 CLAUDE.md 출력 반올림 규칙)
RATE_TO_PERCENT = 100
PERCENT_DECIMALS = 4
STAT_DECIMALS = 2


def summarize_layers(
    frame: pd.DataFrame,
    layers: Sequence[SignalLayer] = tuple(SignalLayer),
    horizons: Sequence[int] = DEFAULT_HORIZONS,
) -> pd.DataFrame:
    """계층 × 보유구간의 초과수익을 집계한다.

    **일자 단위로 먼저 평균을 낸 표본이 기본 검정 대상이다.** 같은 날 같은 테마의 종목은
    수익률이 강하게 상관돼 있어 독립이 아니다. 종목 단위로 t검정을 하면 표준오차가 과소추정되어
    없는 유의성이 생긴다. 종목 단위 평균은 참고로만 병기한다.

    Args:
        frame: 초과수익과 계층 컬럼이 모두 붙은 프레임
        layers: 집계할 계층
        horizons: 보유 구간 목록

    Returns:
        `SUMMARY_COLUMNS` 순서의 집계표

    Raises:
        ValueError: 계층 또는 초과수익 컬럼이 없는 경우
    """
    rows: list[dict[str, object]] = []

    for layer in layers:
        column = layer.name.lower()
        if column not in frame.columns:
            raise ValueError(f"계층 컬럼이 없습니다: {column}")

        member = frame[frame[column]]

        for basis in ReturnBasis:
            for horizon in horizons:
                source = excess_column(basis, horizon)
                if source not in frame.columns:
                    raise ValueError(f"초과수익 컬럼이 없습니다: {source}")

                rows.append(_summarize_cell(member, layer, basis, horizon, source))

    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def _summarize_cell(
    member: pd.DataFrame,
    layer: SignalLayer,
    basis: ReturnBasis,
    horizon: int,
    source: str,
) -> dict[str, object]:
    """계층 × 기준 × 구간 한 칸을 집계한다.

    Args:
        member: 해당 계층에 속한 행
        layer: 신호 계층
        basis: 수익률 시작점
        horizon: 보유 구간
        source: 초과수익 컬럼명

    Returns:
        집계 한 줄
    """
    values = member[[COL_DATE, source]].dropna()
    daily = values.groupby(COL_DATE, observed=True)[source].mean()

    count = len(daily)
    mean = float(daily.mean()) if count else 0.0
    deviation = float(daily.std()) if count > 1 else 0.0
    stderr = deviation / (count**0.5) if count > 1 else 0.0

    return {
        "layer": layer.value,
        "basis": basis.value,
        "horizon": horizon,
        "ticker_count": len(values),
        "ticker_mean_pct": round(float(values[source].mean()) * RATE_TO_PERCENT, PERCENT_DECIMALS)
        if len(values)
        else 0.0,
        "day_count": count,
        "day_mean_pct": round(mean * RATE_TO_PERCENT, PERCENT_DECIMALS),
        "day_std_pct": round(deviation * RATE_TO_PERCENT, PERCENT_DECIMALS),
        "day_stderr_pct": round(stderr * RATE_TO_PERCENT, PERCENT_DECIMALS),
        "day_t_stat": round(mean / stderr, STAT_DECIMALS) if stderr > 0 else 0.0,
        "day_win_rate_pct": round(float((daily > 0).mean()) * RATE_TO_PERCENT, STAT_DECIMALS) if count else 0.0,
    }
