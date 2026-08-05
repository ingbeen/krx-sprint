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
from enum import Enum

import pandas as pd

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
