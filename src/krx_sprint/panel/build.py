"""통합 패널 빌더

1단 원본가 스냅샷과 2단 수정주가를 하나의 (일자, 티커) 패널로 합치고,
수집 단계에서 확정된 처리 규칙을 **플래그 컬럼으로 박아** 저장한다 (백테스트 설계 §2·§3).

설계상 중요한 세 가지.

- **행 집합은 1단이 정한다.** 2단은 1단의 완전한 상위집합이므로(스펙 §0) 왼쪽 조인으로
  결손 없이 붙고, 1단에 없는 이전상장 구간은 조인 과정에서 자동으로 절단된다
- **2단 거래량은 싣지 않는다.** 조정된 거래량(1,147종목)을 거래대금 계산에 쓰면 신호가
  오염된다. 컬럼을 만들지 않는 것이 가장 확실한 차단이다
- **판정 규칙은 품질 리포트와 공유한다.** 상장주식수 급변은 `SharesJumpTracker`,
  수정 미반영은 `is_action_unadjusted`를 그대로 쓴다. 규칙을 복제하면 두 경로가 조용히 갈라진다

패널은 파생 캐시라 1단의 불변 파일 계약을 적용하지 않는다. 빌드는 항상 전량을 다시 쓴다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from krx_sprint.collect.adjusted_quality import PERCENT_TO_RATE, SharesJumpTracker, is_action_unadjusted
from krx_sprint.collect.adjusted_store import load_adjusted
from krx_sprint.collect.snapshot_store import list_collected_dates, load_snapshot
from krx_sprint.common_constants import (
    ADJUSTED_DIR,
    COL_ADJ_CLOSE,
    COL_ADJ_HIGH,
    COL_ADJ_LOW,
    COL_ADJ_OPEN,
    COL_CHANGE_RATE,
    COL_CLOSE,
    COL_DATE,
    COL_HIGH,
    COL_IS_HALTED,
    COL_IS_LAST_SEEN,
    COL_IS_LIMIT_DOWN_CLOSE,
    COL_IS_LIMIT_UP_CLOSE,
    COL_IS_SHARES_JUMP,
    COL_IS_UNADJUSTED_ACTION,
    COL_LOW,
    COL_MARKET,
    COL_NO_REGULAR_SESSION,
    COL_OPEN,
    COL_TICKER,
    COL_VOLUME,
    KST,
    PANEL_COLUMNS,
    PANEL_DIR,
    PANEL_FLAG_COLUMNS,
    SNAPSHOTS_DIR,
)
from krx_sprint.panel.loader import (
    PanelBuildMeta,
    current_fingerprint,
    list_panel_years,
    panel_year_path,
    write_build_meta,
)

# 상한가·하한가 마감 판정 임계 (비율, 0.29 = 29%).
# 가격제한폭은 ±30%지만 호가단위 절사로 실제 등락률은 그보다 조금 낮게 찍힌다.
# 신규상장 첫날처럼 제한폭 밖에서 종가 = 고가가 되는 경우도 걸리지만,
# 그런 날은 어차피 매매 대상이 아니므로 판정을 단순하게 유지한다
LIMIT_CLOSE_RATE = 0.29

# 조인 중간 단계에서만 쓰는 컬럼 (저장하지 않는다)
COL_ADJ_PREV_CLOSE = "adj_prev_close"
COL_DISCLOSED_RATE = "disclosed_rate"


@dataclass(frozen=True)
class BuildSummary:
    """빌드 결과 요약

    Attributes:
        row_count: 패널 행 수
        ticker_count: 패널 종목 수
        year_count: 저장된 연도 파일 수
        first_date: 첫 거래일
        last_date: 마지막 거래일
        flag_counts: 플래그 컬럼별 True 건수
    """

    row_count: int
    ticker_count: int
    year_count: int
    first_date: date
    last_date: date
    flag_counts: dict[str, int]


def build_panel(
    *,
    panel_dir: Path = PANEL_DIR,
    snapshot_dir: Path = SNAPSHOTS_DIR,
    adjusted_dir: Path = ADJUSTED_DIR,
) -> BuildSummary:
    """1·2단을 합쳐 연도별 패널 캐시를 만든다.

    Args:
        panel_dir: 패널 캐시 루트 (기존 파일은 모두 교체된다)
        snapshot_dir: 1단 스냅샷 루트
        adjusted_dir: 2단 수정주가 루트

    Returns:
        빌드 결과 요약

    Raises:
        ValueError: 1단이 없거나, 2단 파일이 빠졌거나, 조인 후 수정주가가 비는 경우
    """
    # 1. 1단 전량 적재 + 상장주식수 급변 관측 (같은 순회에서 끝낸다)
    dates = sorted(list_collected_dates(base_dir=snapshot_dir))
    if not dates:
        raise ValueError("수집된 1단 스냅샷이 없습니다. 먼저 collect_snapshots.py를 실행하십시오")

    raw, tracker = _load_snapshots(dates, snapshot_dir)
    tickers = sorted(set(raw[COL_TICKER]))

    # 2. 2단 조인 — 전일 종가는 절단 전 시계열에서 구해야 경계 판정이 리포트와 같아진다
    panel = _join_adjusted(raw, tickers, adjusted_dir)

    # 3. 처리 규칙 플래그
    panel = _apply_flags(panel, tickers, tracker)

    # 4. 저장
    panel = panel[PANEL_COLUMNS].sort_values([COL_DATE, COL_TICKER]).reset_index(drop=True)
    year_count = _write_years(panel, panel_dir)

    summary = BuildSummary(
        row_count=len(panel),
        ticker_count=len(tickers),
        year_count=year_count,
        first_date=dates[0],
        last_date=dates[-1],
        flag_counts={column: int(panel[column].sum()) for column in PANEL_FLAG_COLUMNS},
    )

    write_build_meta(
        PanelBuildMeta(
            fingerprint=current_fingerprint(snapshot_dir, adjusted_dir),
            row_count=summary.row_count,
            ticker_count=summary.ticker_count,
            built_at=datetime.now(KST).isoformat(timespec="seconds"),
        ),
        panel_dir,
    )

    return summary


def _load_snapshots(dates: Sequence[date], snapshot_dir: Path) -> tuple[pd.DataFrame, SharesJumpTracker]:
    """1단 스냅샷을 전량 적재하면서 상장주식수 급변을 관측한다.

    급변 판정은 직전 등장일과의 비교라 일자 오름차순 순회가 전제다.

    Args:
        dates: 1단 수집 일자 (오름차순)
        snapshot_dir: 1단 스냅샷 루트

    Returns:
        (일자 컬럼이 붙은 1단 전량, 급변 추적기)
    """
    tracker = SharesJumpTracker()
    frames: list[pd.DataFrame] = []

    for target in dates:
        snapshot = load_snapshot(target, base_dir=snapshot_dir)
        tracker.observe(target, snapshot)
        frames.append(snapshot.assign(**{COL_DATE: pd.Timestamp(target)}))

    return pd.concat(frames, ignore_index=True), tracker


def _join_adjusted(raw: pd.DataFrame, tickers: Sequence[str], adjusted_dir: Path) -> pd.DataFrame:
    """2단 수정주가를 1단 행에 왼쪽 조인한다.

    Args:
        raw: 일자 컬럼이 붙은 1단 전량
        tickers: 1단 유니버스 (오름차순)
        adjusted_dir: 2단 수정주가 루트

    Returns:
        수정주가 컬럼이 붙은 패널

    Raises:
        ValueError: 2단 파일이 없거나, 조인으로 행 수가 바뀌거나, 수정주가가 비는 경우
    """
    adjusted = _load_adjusted_frame(tickers, adjusted_dir)
    joined = raw.merge(adjusted, on=[COL_DATE, COL_TICKER], how="left")

    if len(joined) != len(raw):
        raise RuntimeError(f"내부 불변조건 위반: 2단 조인으로 행 수가 바뀌었습니다 (raw={len(raw)}, joined={len(joined)})")

    missing = int(joined[COL_ADJ_CLOSE].isna().sum())
    if missing > 0:
        sample = joined[joined[COL_ADJ_CLOSE].isna()].iloc[0]
        raise ValueError(
            f"2단 수정주가가 비는 (티커, 일자) 조합이 {missing:,}건 있습니다 "
            f"(예: {sample[COL_TICKER]} {sample[COL_DATE].date().isoformat()}). "
            "2단이 1단보다 과거에 멈춰 있을 수 있습니다 — collect_adjusted.py를 다시 실행하십시오"
        )

    return joined


def _load_adjusted_frame(tickers: Sequence[str], adjusted_dir: Path) -> pd.DataFrame:
    """2단 시계열을 티커 컬럼이 붙은 하나의 프레임으로 모은다.

    전일 수정 종가는 **절단 전 전체 시계열**에서 구한다. 이전상장 경계에서 직전 거래일이
    달라지면 수정 미반영 판정이 품질 리포트와 어긋나기 때문이다.

    Args:
        tickers: 대상 티커 (오름차순)
        adjusted_dir: 2단 수정주가 루트

    Returns:
        (일자, 티커) + 수정주가 + 전일 수정 종가 프레임

    Raises:
        ValueError: 2단 파일이 없는 티커가 있는 경우
    """
    frames: list[pd.DataFrame] = []

    for ticker in tickers:
        try:
            series = load_adjusted(ticker, base_dir=adjusted_dir)
        except FileNotFoundError as error:
            raise ValueError(f"2단 수정주가 파일이 없습니다: {ticker}. collect_adjusted.py를 먼저 실행하십시오") from error

        frames.append(
            pd.DataFrame(
                {
                    COL_DATE: series[COL_DATE],
                    COL_TICKER: ticker,
                    COL_ADJ_OPEN: series[COL_OPEN],
                    COL_ADJ_HIGH: series[COL_HIGH],
                    COL_ADJ_LOW: series[COL_LOW],
                    COL_ADJ_CLOSE: series[COL_CLOSE],
                    COL_ADJ_PREV_CLOSE: series[COL_CLOSE].shift(1),
                }
            )
        )

    return pd.concat(frames, ignore_index=True)


def _apply_flags(panel: pd.DataFrame, tickers: Sequence[str], tracker: SharesJumpTracker) -> pd.DataFrame:
    """처리 규칙 플래그를 계산해 붙인다.

    Args:
        panel: 수정주가가 조인된 패널
        tickers: 1단 유니버스
        tracker: 상장주식수 급변 추적기

    Returns:
        플래그가 붙은 패널
    """
    flagged = panel.copy()

    # 1. 행 단위로 판정 가능한 규칙
    change_rate = flagged[COL_CHANGE_RATE] / PERCENT_TO_RATE
    traded = flagged[COL_VOLUME] > 0

    flagged[COL_IS_HALTED] = flagged[COL_VOLUME] == 0
    flagged[COL_NO_REGULAR_SESSION] = traded & (flagged[COL_LOW] == 0)
    flagged[COL_IS_LIMIT_UP_CLOSE] = (
        traded & (change_rate >= LIMIT_CLOSE_RATE) & (flagged[COL_CLOSE] == flagged[COL_HIGH])
    )
    flagged[COL_IS_LIMIT_DOWN_CLOSE] = (
        traded & (change_rate <= -LIMIT_CLOSE_RATE) & (flagged[COL_CLOSE] == flagged[COL_LOW])
    )

    # 2. 상장주식수 급변 — 품질 리포트와 같은 추적기가 판정한 일자를 그대로 옮긴다
    flagged = flagged.merge(_jump_frame(tickers, tracker), on=[COL_DATE, COL_TICKER], how="left")
    flagged[COL_IS_SHARES_JUMP] = flagged[COL_DISCLOSED_RATE].notna()

    # 3. 수정 미반영 — 급변일 중 전일 종가가 유효한 행만 공유 판별식에 넘긴다
    flagged[COL_IS_UNADJUSTED_ACTION] = _unadjusted_flags(flagged)

    # 4. 티커별 최종 등장일. 폐지가 아니라 "마지막으로 보인 날"이므로,
    #    수집 마지막 거래일에 남아 있는 종목도 True다 — 폐지 판정은 소비 시점에 구간과 함께 본다
    flagged[COL_IS_LAST_SEEN] = flagged[COL_DATE] == flagged.groupby(COL_TICKER, observed=True)[COL_DATE].transform(
        "max"
    )

    for column in PANEL_FLAG_COLUMNS:
        flagged[column] = flagged[column].astype(bool)

    flagged[COL_TICKER] = pd.Categorical(flagged[COL_TICKER], categories=list(tickers))
    flagged[COL_MARKET] = flagged[COL_MARKET].astype("category")

    return flagged


def _jump_frame(tickers: Sequence[str], tracker: SharesJumpTracker) -> pd.DataFrame:
    """상장주식수 급변일과 그날의 공시 등락률을 프레임으로 만든다.

    Args:
        tickers: 1단 유니버스
        tracker: 급변 추적기

    Returns:
        (일자, 티커, 공시 등락률) 프레임 (급변이 없으면 빈 프레임)
    """
    records: list[tuple[pd.Timestamp, str, float]] = [
        (pd.Timestamp(observation.target), ticker, observation.disclosed_rate)
        for ticker in tickers
        for observation in tracker.observations_for(ticker)
    ]

    if not records:
        return pd.DataFrame({COL_DATE: pd.Series(dtype="datetime64[ns]"), COL_TICKER: [], COL_DISCLOSED_RATE: []})

    return pd.DataFrame(records, columns=[COL_DATE, COL_TICKER, COL_DISCLOSED_RATE])


def _unadjusted_flags(panel: pd.DataFrame) -> pd.Series:
    """급변일의 수정계수 미반영 여부를 판정한다.

    판별식은 품질 리포트와 공유하는 `is_action_unadjusted`다. 급변일은 전체의 극소수이므로
    벡터화 대신 해당 행만 순회한다 — 규칙을 다시 쓰지 않는 것이 우선이다.

    Args:
        panel: 급변 플래그와 전일 수정 종가가 붙은 패널

    Returns:
        행별 미반영 여부
    """
    flags = pd.Series(False, index=panel.index)

    previous = panel[COL_ADJ_PREV_CLOSE]
    target = panel[COL_IS_SHARES_JUMP] & previous.notna() & (previous > 0)
    if not target.any():
        return flags

    rates = panel.loc[target, COL_ADJ_CLOSE] / previous[target] - 1
    disclosed = panel.loc[target, COL_DISCLOSED_RATE]
    judged = pd.Series(
        [is_action_unadjusted(float(rate), float(value)) for rate, value in zip(rates, disclosed, strict=True)],
        index=rates.index,
        dtype=bool,
    )
    flags.loc[target] = judged

    return flags


def _write_years(panel: pd.DataFrame, panel_dir: Path) -> int:
    """연도별 parquet으로 저장한다. 이전 산출물은 남기지 않는다.

    Args:
        panel: 저장할 패널 (`PANEL_COLUMNS` 스키마, 정렬 완료)
        panel_dir: 패널 캐시 루트

    Returns:
        저장한 연도 파일 수
    """
    panel_dir.mkdir(parents=True, exist_ok=True)
    for year in list_panel_years(panel_dir):
        panel_year_path(year, panel_dir).unlink()

    years = 0
    for year, group in panel.groupby(panel[COL_DATE].dt.year, observed=True):
        group.reset_index(drop=True).to_parquet(panel_year_path(int(year), panel_dir), index=False)
        years += 1

    return years
