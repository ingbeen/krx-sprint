"""테마 동조화 클러스터링

백테스트 설계 §4의 확정안 B를 코드로 옮긴다.

외부 테마 분류(네이버·증권사)는 과거로 소급할 수 없어 미래참조 편향이 된다. 대신 매일의
가격으로 동조화 클러스터를 **그날의 데이터만으로** 다시 만든다.

설계상 중요한 두 가지.

- **단독 급등은 테마가 아니다.** 클러스터 크기 하한이 이 규칙을 직접 표현한다
- **시장 팩터를 제거한 잔차로 상관을 본다.** 지수가 통째로 오른 날 전 종목이 한 테마가 되는
  것을 막는 장치다. 잔차가 남지 않으면(순수 시장 움직임) 상관이 정의되지 않아 엣지가 생기지 않는다

계산은 **당일 급등 종목 부분집합**에만 돌린다. 하루 수십 종목이라 상관 계산이 가볍고,
전종목 상관행렬(약 2,500²)을 매일 돌리는 비용을 피한다.
"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

import pandas as pd

from krx_sprint.backtest.params import StrategyParams
from krx_sprint.collect.adjusted_quality import PERCENT_TO_RATE
from krx_sprint.common_constants import (
    COL_ADJ_CLOSE,
    COL_CHANGE_RATE,
    COL_DATE,
    COL_IS_HALTED,
    COL_IS_SHARES_JUMP,
    COL_MARKET_CAP,
    COL_TICKER,
    COL_VALUE,
)


@dataclass(frozen=True)
class ThemeCluster:
    """하루치 동조화 클러스터

    Attributes:
        tickers: 클러스터 구성 종목 (오름차순)
        leader: 대장주. 클러스터 안에서 당일 거래대금(원본가) 1위
    """

    tickers: tuple[str, ...]
    leader: str


def find_clusters(
    panel: pd.DataFrame,
    target: date,
    params: StrategyParams,
    *,
    listed_days: Mapping[str, int] | None = None,
) -> tuple[ThemeCluster, ...]:
    """신호일의 테마 클러스터를 찾는다.

    **신호일까지의 데이터만 쓴다.** 패널에 이후 구간이 들어 있어도 결과가 달라지지 않는다
    (look-ahead 방지).

    Args:
        panel: 통합 패널. 신호일 이전 구간을 상관 창 길이 이상 담고 있어야 한다
        target: 신호일
        params: 전략 파라미터
        listed_days: 티커별 상장 경과 거래일. 엔진이 매일 호출할 때 넘겨 재계산을 피한다.
            생략하면 `panel`에서 센다 — 이때 `panel`이 전체 이력이어야 값이 맞다

    Returns:
        클러스터 (구성 종목 오름차순으로 정렬)

    Raises:
        ValueError: 패널에 신호일 데이터가 없는 경우
    """
    history = panel[panel[COL_DATE] <= pd.Timestamp(target)]
    if history.empty:
        raise ValueError(f"신호일까지의 패널 데이터가 없습니다: {target.isoformat()}")

    today = history[history[COL_DATE] == pd.Timestamp(target)]
    if today.empty:
        raise ValueError(f"신호일 데이터가 패널에 없습니다: {target.isoformat()}")

    # 1. 유니버스 게이트 → 당일 급등 집합
    universe = _apply_universe_gates(history, today, params, listed_days)
    surged = _surged_tickers(today, universe, params)
    if len(surged) < params.min_cluster_size:
        return ()

    # 2. 상관 창의 수익률 (잔차 옵션 적용)
    returns = _window_returns(history, universe, params.correlation_window)
    if returns.empty:
        return ()

    if params.use_residual_correlation:
        returns = _residual_returns(returns)

    available = [ticker for ticker in surged if ticker in returns.columns]
    if len(available) < params.min_cluster_size:
        return ()

    # 3. 잔차 상관 그래프 → 연결요소
    correlation = returns[available].corr()
    components = _connected_components(available, correlation, params.correlation_threshold)

    # 4. 대장주 = 당일 거래대금 1위
    values = dict(zip(today[COL_TICKER].astype(str), today[COL_VALUE], strict=True))
    clusters = [
        ThemeCluster(tickers=tuple(sorted(component)), leader=max(component, key=lambda item: values[item]))
        for component in components
        if len(component) >= params.min_cluster_size
    ]

    return tuple(sorted(clusters, key=lambda cluster: cluster.tickers))


def _apply_universe_gates(
    history: pd.DataFrame,
    today: pd.DataFrame,
    params: StrategyParams,
    listed_days: Mapping[str, int] | None,
) -> set[str]:
    """유니버스 게이트를 통과한 종목을 고른다 (설계 §4.2).

    Args:
        history: 신호일까지의 패널
        today: 신호일 행
        params: 전략 파라미터
        listed_days: 티커별 상장 경과 거래일 (없으면 `history`에서 센다)

    Returns:
        통과한 티커 집합
    """
    elapsed = listed_days if listed_days is not None else history[COL_TICKER].astype(str).value_counts().to_dict()

    passed = today[
        ~today[COL_IS_HALTED] & ~today[COL_IS_SHARES_JUMP] & (today[COL_MARKET_CAP] >= params.min_market_cap)
    ]

    return {
        ticker for ticker in passed[COL_TICKER].astype(str) if int(elapsed.get(ticker, 0)) >= params.min_listed_days
    }


def _surged_tickers(today: pd.DataFrame, universe: set[str], params: StrategyParams) -> list[str]:
    """당일 급등 종목을 고른다.

    등락률은 **1단 원본가** 기준이다 (스펙 §10.3).

    Args:
        today: 신호일 행
        universe: 게이트를 통과한 티커
        params: 전략 파라미터

    Returns:
        급등 티커 (오름차순)
    """
    rate = today[COL_CHANGE_RATE] / PERCENT_TO_RATE
    surged = today[rate >= params.co_move_rate]

    return sorted(ticker for ticker in surged[COL_TICKER].astype(str) if ticker in universe)


def _window_returns(history: pd.DataFrame, universe: set[str], window: int) -> pd.DataFrame:
    """상관 창의 일간 수익률 행렬을 만든다.

    수익률은 **2단 수정주가**로 계산한다 — 분할·배당락이 흡수돼 있어야 상관이 왜곡되지 않는다.
    창을 채우지 못한 종목은 제외한다(결측을 0으로 메우지 않는다).

    Args:
        history: 신호일까지의 패널
        universe: 게이트를 통과한 티커
        window: 수익률 개수 (필요한 종가 수는 이보다 하나 많다)

    Returns:
        (일자 × 티커) 수익률 행렬
    """
    dates = sorted(history[COL_DATE].unique())[-(window + 1) :]
    if len(dates) < 2:
        return pd.DataFrame()

    rows = history[history[COL_DATE].isin(dates) & history[COL_TICKER].astype(str).isin(universe)]
    prices = rows.pivot_table(index=COL_DATE, columns=COL_TICKER, values=COL_ADJ_CLOSE, observed=True)

    # 0 이하 종가(거래정지)가 섞이면 수익률이 무한대가 된다 — 해당 종목을 통째로 뺀다
    prices = prices.loc[:, (prices > 0).all()]

    return prices.pct_change().dropna(how="all")


def _residual_returns(returns: pd.DataFrame) -> pd.DataFrame:
    """시장 팩터를 제거한 잔차 수익률을 구한다.

    시장 수익률은 유니버스 동일가중 평균이며, 종목별 베타는 같은 창에서 추정한다.
    잔차가 정확히 0이면(순수 시장 움직임) 상관이 정의되지 않아 이후 단계에서 엣지가 생기지 않는다 —
    의도된 동작이다.

    Args:
        returns: (일자 × 티커) 수익률 행렬

    Returns:
        같은 형태의 잔차 행렬
    """
    market = returns.mean(axis=1)
    market_variance = float(market.var())

    if market_variance == 0:
        return returns

    betas = returns.apply(lambda column: column.cov(market) / market_variance)

    return returns - pd.DataFrame({ticker: market * betas[ticker] for ticker in returns.columns})


def _connected_components(
    tickers: Iterable[str],
    correlation: pd.DataFrame,
    threshold: float,
) -> list[set[str]]:
    """상관 임계를 넘는 엣지로 연결요소를 만든다.

    상관이 NaN이면 엣지를 만들지 않는다 — 잔차가 남지 않은 경우이며, 근거 없이 묶지 않는다.

    Args:
        tickers: 후보 티커
        correlation: 상관 행렬
        threshold: 엣지 판정 임계

    Returns:
        연결요소 목록
    """
    nodes = list(tickers)
    adjacency: dict[str, set[str]] = {ticker: set() for ticker in nodes}

    # 상관 행렬을 numpy로 꺼내 쓴다 — 라벨 조회보다 빠르고 값 타입이 float으로 확정된다
    matrix = correlation.to_numpy(dtype=float)
    positions = {str(ticker): position for position, ticker in enumerate(correlation.columns)}

    for index, left in enumerate(nodes):
        for right in nodes[index + 1 :]:
            value = float(matrix[positions[left], positions[right]])
            if not math.isnan(value) and value >= threshold:
                adjacency[left].add(right)
                adjacency[right].add(left)

    components: list[set[str]] = []
    unvisited = set(nodes)

    while unvisited:
        component: set[str] = set()
        queue = [unvisited.pop()]

        while queue:
            node = queue.pop()
            component.add(node)
            for neighbor in adjacency[node]:
                if neighbor in unvisited:
                    unvisited.remove(neighbor)
                    queue.append(neighbor)

        components.append(component)

    return components
