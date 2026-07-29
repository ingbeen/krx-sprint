"""2단 수정주가 정합성 검사

저장된 수정주가 시계열을 1단 원본 스냅샷과 대조해 스펙 §8의 "2단 정합성"을 판정한다.

핵심 계약은 세 가지다.

- **최신 일자의 수정 종가 = 1단 원본 종가**. 수정주가는 조회 시점 기준으로 과거를 조정하므로
  조회 종료일 이후에 액션이 없었다면 최신일 값은 원본과 같아야 한다. 어긋나면 그 사이에
  액션이 발생했거나 조정 기준이 달라진 것이다 (스펙 §0 실측)
- **상장주식수 급변일에 수정계수가 반영됐을 것**. 판별식은 2단 변동이 **공시 등락률**과
  일치하는가다. 가격제한폭 초과 여부로만 보면 상한가·하한가가 겹친 정상 사례를 오탐한다
- **1단 최초 등장일 경계가 연속일 것**. 2단은 1단이 제외한 시장의 이전상장 이력까지 담고 있고
  그 구간은 가격 축이 다르다

이 모듈은 데이터를 수정하지 않는다. 탐지·보고만 하고 조치는 사람이 판단한다.
심각도 체계는 1단 품질 검사(`quality.py`)와 공유한다.
"""

from collections.abc import Collection, Container
from dataclasses import dataclass
from datetime import date

import pandas as pd

from krx_sprint.collect.quality import SAMPLE_LIMIT, SHARES_CHANGE_WARN_RATE, QualityIssue, Severity
from krx_sprint.common_constants import (
    ADJUSTED_COLUMNS,
    ADJUSTED_PRICE_COLUMNS,
    COL_CHANGE_RATE,
    COL_CLOSE,
    COL_DATE,
    COL_HIGH,
    COL_LOW,
    COL_SHARES,
    COL_TICKER,
    COL_VOLUME,
    PRICE_LIMIT_RATE,
)

# 2단 변동과 공시 등락률의 허용 오차 (비율, 0.01 = 1%p).
# 실측상 정상 사례의 편차는 최대 0.0007, 수정 미반영 사례의 최소 편차는 0.4994로 간격이 크다
ACTION_RATE_TOLERANCE = 0.01

# 백분율(%) → 비율 변환 계수. pykrx 등락률은 % 단위로 저장된다 (루트 CLAUDE.md 비율 표기 규칙)
PERCENT_TO_RATE = 100.0


@dataclass(frozen=True)
class ActionObservation:
    """상장주식수가 급변한 일자의 1단 관측치

    수정계수가 적용됐는지 판정하려면 2단 변동만으로는 부족하다 — 그날의 실제 등락
    (권리락 기준가 대비 공시 등락률)과 원본 종가의 겉보기 변동을 함께 봐야 한다.

    Attributes:
        target: 급변 일자
        disclosed_rate: 공시 등락률 (비율, 0.30 = 30%)
        raw_rate: 원본 종가의 전일 대비 변동 (비율). 직전 종가가 없거나 0이면 NaN
    """

    target: date
    disclosed_rate: float
    raw_rate: float


@dataclass(frozen=True)
class SeriesSummary:
    """2단 시계열 1개의 요약

    최신일 종가 대조는 시계열 전체가 아니라 이 요약만 있으면 되므로,
    전량을 메모리에 올리지 않기 위해 분리한다.

    Attributes:
        ticker: 대상 티커
        first_date: 시계열 시작 일자
        last_date: 시계열 종료 일자
        last_close: 종료 일자의 수정 종가
        row_count: 행 수
    """

    ticker: str
    first_date: date
    last_date: date
    last_close: float
    row_count: int


def _require_schema(frame: pd.DataFrame) -> None:
    """시계열 스키마를 확인한다.

    Args:
        frame: 검사 대상 시계열

    Raises:
        ValueError: 필요한 컬럼이 없는 경우
    """
    missing = [column for column in ADJUSTED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"시계열에 필요한 컬럼이 없습니다: {missing}")


def check_collection_coverage(
    universe: Collection[str],
    collected: Container[str],
) -> tuple[QualityIssue, ...]:
    """1단 유니버스가 2단에 모두 수집됐는지 검사한다.

    빠진 종목이 있으면 그만큼 생존편향이 2단에서 되살아난다 (스펙 §3.2).

    Args:
        universe: 1단 스냅샷 합집합의 티커
        collected: 2단 수집이 끝난 티커

    Returns:
        결손 티커별 이슈
    """
    return tuple(
        QualityIssue(
            severity=Severity.ERROR,
            category="수집 결손",
            target=ticker,
            detail="1단 유니버스에 있으나 2단 수정주가 파일이 없습니다",
        )
        for ticker in sorted(universe)
        if ticker not in collected
    )


def summarize_adjusted(frame: pd.DataFrame, ticker: str) -> SeriesSummary:
    """시계열의 구간과 마지막 종가를 요약한다.

    Args:
        frame: 대상 시계열 (일자 오름차순)
        ticker: 대상 티커

    Returns:
        요약 결과

    Raises:
        ValueError: 시계열이 비었거나 스키마가 다른 경우
    """
    _require_schema(frame)

    if frame.empty:
        raise ValueError(f"시계열이 비어 있어 요약할 수 없습니다: {ticker}")

    return SeriesSummary(
        ticker=ticker,
        first_date=frame[COL_DATE].iloc[0].date(),
        last_date=frame[COL_DATE].iloc[-1].date(),
        last_close=float(frame[COL_CLOSE].iloc[-1]),
        row_count=len(frame),
    )


def check_latest_close(summary: SeriesSummary, snapshot_close: int | None) -> tuple[QualityIssue, ...]:
    """최신 일자의 수정 종가가 1단 원본 종가와 같은지 검사한다 (스펙 §8).

    수정주가는 최신일 기준으로 과거를 조정하므로 최신일 값은 원본과 정확히 같아야 한다.
    근사 비교를 쓰지 않는 이유는, 미세한 차이도 조정 기준이 달라졌다는 신호이기 때문이다.

    Args:
        summary: 2단 시계열 요약
        snapshot_close: 같은 일자 1단 스냅샷의 해당 종목 종가 (없으면 None)

    Returns:
        불일치 이슈 (일치하면 빈 튜플)
    """
    if snapshot_close is None:
        return (
            QualityIssue(
                severity=Severity.ERROR,
                category="최신일 종가 대조",
                target=summary.ticker,
                detail=f"{summary.last_date.isoformat()} 1단 스냅샷에 해당 종목이 없어 대조할 수 없습니다",
            ),
        )

    if summary.last_close == float(snapshot_close):
        return ()

    return (
        QualityIssue(
            severity=Severity.ERROR,
            category="최신일 종가 대조",
            target=summary.ticker,
            detail=(
                f"{summary.last_date.isoformat()} 수정 종가 {summary.last_close} ≠ "
                f"원본 종가 {snapshot_close}. 수정주가가 최신일 기준으로 조정되지 않았습니다"
            ),
        ),
    )


def _check_series_dates(frame: pd.DataFrame, ticker: str, snapshot_dates: Container[date]) -> list[QualityIssue]:
    """일자 정렬·중복과 1단 거래일 정합을 검사한다."""
    issues: list[QualityIssue] = []
    dates = frame[COL_DATE]

    if not bool(dates.is_monotonic_increasing):
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                category="일자 정렬",
                target=ticker,
                detail="일자가 오름차순이 아닙니다",
            )
        )

    duplicated = dates.duplicated()
    if bool(duplicated.any()):
        sample = [str(value.date()) for value in dates[duplicated].head(SAMPLE_LIMIT)]
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                category="일자 중복",
                target=ticker,
                detail=f"중복 일자 {int(duplicated.sum())}건 (예: {sample})",
            )
        )

    # 1단에 없는 거래일이 2단에 있으면 대조가 불가능한 구간이 생긴다
    observed = {value.date() for value in dates}
    unknown = sorted(value for value in observed if value not in snapshot_dates)
    if unknown:
        sample = [value.isoformat() for value in unknown[:SAMPLE_LIMIT]]
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                category="거래일 정합",
                target=ticker,
                detail=f"1단 수집 일자에 없는 거래일 {len(unknown)}건 (예: {sample})",
            )
        )

    return issues


def _check_series_prices(frame: pd.DataFrame, ticker: str) -> list[QualityIssue]:
    """가격 정합성을 검사한다 (음수·거래 중 종가 없음·고가 < 저가)."""
    issues: list[QualityIssue] = []

    negative = frame[ADJUSTED_PRICE_COLUMNS].lt(0).any(axis=1)
    if bool(negative.any()):
        sample = [str(value.date()) for value in frame.loc[negative, COL_DATE].head(SAMPLE_LIMIT)]
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                category="가격 정합성",
                target=ticker,
                detail=f"음수 가격 {int(negative.sum())}일 (예: {sample})",
            )
        )

    invalid_close = (frame[COL_VOLUME] > 0) & (frame[COL_CLOSE] <= 0)
    if bool(invalid_close.any()):
        sample = [str(value.date()) for value in frame.loc[invalid_close, COL_DATE].head(SAMPLE_LIMIT)]
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                category="가격 정합성",
                target=ticker,
                detail=f"거래가 있는데 종가 0 이하 {int(invalid_close.sum())}일 (예: {sample})",
            )
        )

    high_low = frame[COL_HIGH] < frame[COL_LOW]
    if bool(high_low.any()):
        sample = [str(value.date()) for value in frame.loc[high_low, COL_DATE].head(SAMPLE_LIMIT)]
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                category="가격 정합성",
                target=ticker,
                detail=f"고가 < 저가 {int(high_low.sum())}일 (예: {sample})",
            )
        )

    return issues


def check_adjusted_series(
    frame: pd.DataFrame,
    ticker: str,
    snapshot_dates: Container[date],
) -> tuple[QualityIssue, ...]:
    """한 종목 시계열의 구조와 가격 정합성을 검사한다.

    Args:
        frame: 대상 시계열
        ticker: 대상 티커
        snapshot_dates: 1단 수집 일자 집합

    Returns:
        발견된 이슈 (없으면 빈 튜플)

    Raises:
        ValueError: 스키마가 규칙과 다른 경우
    """
    # 1. 구조 확인
    _require_schema(frame)

    if frame.empty:
        return (
            QualityIssue(
                severity=Severity.ERROR,
                category="빈 시계열",
                target=ticker,
                detail="행이 없는 수정주가 파일입니다",
            ),
        )

    # 2. 개별 검사
    issues = _check_series_dates(frame, ticker, snapshot_dates)
    issues.extend(_check_series_prices(frame, ticker))

    return tuple(issues)


def _rate_at(frame: pd.DataFrame, position: int) -> float | None:
    """해당 위치의 전일 대비 수정 종가 변동률을 구한다.

    Args:
        frame: 대상 시계열 (일자 오름차순)
        position: 대상 행 위치

    Returns:
        변동률 (비율). 첫 행이거나 직전 종가가 0 이하면 None
    """
    if position <= 0:
        return None

    closes = frame[COL_CLOSE].to_numpy()
    previous_close = float(closes[position - 1])
    if previous_close <= 0:
        return None

    return float(closes[position]) / previous_close - 1


def _positions_by_date(frame: pd.DataFrame) -> dict[date, int]:
    """일자 → 행 위치 매핑을 만든다."""
    return {value.date(): index for index, value in enumerate(frame[COL_DATE])}


def is_action_unadjusted(adjusted_rate: float, disclosed_rate: float) -> bool:
    """상장주식수 급변일에 수정계수가 반영되지 않았는지 판정한다 (스펙 §8.2).

    판별식은 **2단 변동이 공시 등락률과 일치하는가**다. 일치하면 수정계수가 적용된 정상이고,
    어긋나면 원본가가 그대로 남은 미반영이다. "가격제한폭을 넘는가"로만 보면 상한가·하한가가
    액션일과 겹친 정상 사례를 오탐한다(실측 33건 중 17건).

    단 감자 후 거래재개처럼 KRX가 기준가를 조정하지 않아 **공시 등락률 자체가 왜곡**되는 경우가
    있어(`052670` 2026-02-09 +29,948%), 등락률이 가격제한폭 안에 있을 때만 일치 판정을 적용한다.

    품질 리포트와 백테스트 통합 패널이 **같은 판정을 쓰도록** 순수 함수로 분리했다.
    두 경로가 갈라지면 리포트와 백테스트가 서로 다른 데이터를 보게 된다.

    Args:
        adjusted_rate: 2단 수정 종가의 전일 대비 변동 (비율)
        disclosed_rate: 1단 공시 등락률 (비율)

    Returns:
        수정계수가 반영되지 않았으면 True
    """
    # 가격제한폭 이내면 액션이 흡수된 상태다
    if abs(adjusted_rate) <= PRICE_LIMIT_RATE:
        return False

    # 공시 등락률과 일치하면 상한가·하한가가 액션일과 겹친 정상 사례다
    disclosed_is_usable = abs(disclosed_rate) <= PRICE_LIMIT_RATE
    if disclosed_is_usable and abs(adjusted_rate - disclosed_rate) <= ACTION_RATE_TOLERANCE:
        return False

    return True


def check_action_continuity(
    frame: pd.DataFrame,
    ticker: str,
    observations: Collection[ActionObservation],
) -> tuple[QualityIssue, ...]:
    """상장주식수 급변일에 수정계수가 반영됐는지 검사한다 (스펙 §8.2·§10.5).

    판별식은 `is_action_unadjusted`에 있다. 이 함수는 급변일마다 전일 대비 변동을 구해
    그 판별식에 넘기고, 결과를 이슈로 옮기는 일만 한다.

    수정 미반영은 데이터 오류가 아니라 KRX가 조정하지 않은 액션이므로 경고로 남긴다.
    백테스트에서 해당 일자를 수익률·스윙 계산에서 제외해야 한다.

    Args:
        frame: 대상 시계열 (일자 오름차순)
        ticker: 대상 티커
        observations: 1단에서 상장주식수가 급변한 일자의 관측치

    Returns:
        수정 미반영 이슈 (없으면 빈 튜플)

    Raises:
        ValueError: 스키마가 규칙과 다른 경우
    """
    _require_schema(frame)

    if frame.empty or not observations:
        return ()

    positions = _positions_by_date(frame)
    issues: list[QualityIssue] = []

    for observation in sorted(observations, key=lambda item: item.target):
        # 시계열 밖이거나 첫 행이면 전 거래일 종가가 없어 판정할 수 없다
        position = positions.get(observation.target)
        if position is None:
            continue

        adjusted_rate = _rate_at(frame, position)
        if adjusted_rate is None:
            continue

        if not is_action_unadjusted(adjusted_rate, observation.disclosed_rate):
            continue

        issues.append(
            QualityIssue(
                severity=Severity.WARNING,
                category="수정 미반영",
                target=ticker,
                detail=(
                    f"{observation.target.isoformat()} 상장주식수 급변일의 수정 종가 변동 {adjusted_rate:.4f}이 "
                    f"공시 등락률 {observation.disclosed_rate:.4f}과 다릅니다 "
                    f"(원본 종가 변동 {observation.raw_rate:.4f}). 수정계수가 반영되지 않아 가짜 갭이 남아 있습니다"
                ),
            )
        )

    return tuple(issues)


def check_listing_boundary(
    frame: pd.DataFrame,
    ticker: str,
    first_seen: date | None,
) -> tuple[QualityIssue, ...]:
    """1단 최초 등장일 경계에서 가격 축이 어긋나는지 검사한다 (스펙 §8.2).

    1단은 코넥스를 제외하지만(스펙 §5) 2단은 티커 단위 조회라 이전상장 전 이력까지 반환한다.
    그 구간은 상장 이후와 가격 축이 달라 경계에서 불연속이 생기며, 그대로 쓰면 가짜 수익률이 된다.
    1단 상장주식수 급변일을 트리거로 쓰는 `check_action_continuity`로는 1단 데이터가 없는
    이 경계를 원리적으로 잡을 수 없다.

    데이터는 KRX 원본 그대로이고 조치가 "소비 시점 절단"으로 정해져 있으므로 경고로 남긴다.

    Args:
        frame: 대상 시계열 (일자 오름차순)
        ticker: 대상 티커
        first_seen: 1단 최초 등장일 (알 수 없으면 None)

    Returns:
        경계 불연속 이슈 (없으면 빈 튜플)

    Raises:
        ValueError: 스키마가 규칙과 다른 경우
    """
    _require_schema(frame)

    if frame.empty or first_seen is None:
        return ()

    position = _positions_by_date(frame).get(first_seen)
    if position is None:
        return ()

    # 첫 행이면 이전상장 구간이 없다 (정상)
    rate = _rate_at(frame, position)
    if rate is None or abs(rate) <= PRICE_LIMIT_RATE:
        return ()

    return (
        QualityIssue(
            severity=Severity.WARNING,
            category="이전상장 경계",
            target=ticker,
            detail=(
                f"{first_seen.isoformat()} 1단 최초 등장일의 수정 종가 변동 {rate:.4f}이 "
                f"가격제한폭({PRICE_LIMIT_RATE:.2f})을 넘습니다. "
                f"이전 구간은 가격 축이 달라 소비 시점에 절단해야 합니다"
            ),
        ),
    )


class SharesJumpTracker:
    """1단 스냅샷을 순회하며 상장주식수 급변일의 관측치를 티커별로 모은다.

    증자·감자·액면분할·병합이 있은 날이며, 2단 수정주가가 이를 조정했는지 확인하는
    기준점이 된다 (스펙 §10.5). 일자를 오름차순으로 `observe`에 넘긴다.

    직전 등장일과의 단순 비교라 티커 재사용 구간(스펙 §10.4)도 급변으로 잡힐 수 있다.
    판정 결과가 경고이므로 사람이 확인하는 것으로 충분하다.
    """

    def __init__(self, change_rate: float = SHARES_CHANGE_WARN_RATE) -> None:
        """추적기를 초기화한다.

        Args:
            change_rate: 급변으로 판정할 변동 비율 (0.10 = 10%)
        """
        self._change_rate = change_rate
        self._last_shares: dict[str, int] = {}
        self._last_close: dict[str, int] = {}
        self._observations: dict[str, list[ActionObservation]] = {}

    def observe(self, target: date, snapshot: pd.DataFrame) -> None:
        """한 일자 스냅샷을 반영한다.

        급변일이면 공시 등락률과 원본 종가 변동을 함께 기록한다 —
        수정계수 반영 여부 판정에 둘 다 필요하다.

        Args:
            target: 관측 일자
            snapshot: 해당 일자 스냅샷 (티커·상장주식수·종가·등락률 컬럼 필요)

        Raises:
            ValueError: 필요한 컬럼이 없는 경우
        """
        required = (COL_TICKER, COL_SHARES, COL_CLOSE, COL_CHANGE_RATE)
        missing = [column for column in required if column not in snapshot.columns]
        if missing:
            raise ValueError(f"스냅샷에 필요한 컬럼이 없습니다: {missing}")

        for ticker, raw_shares, raw_close, raw_change_rate in zip(
            snapshot[COL_TICKER],
            snapshot[COL_SHARES],
            snapshot[COL_CLOSE],
            snapshot[COL_CHANGE_RATE],
            strict=True,
        ):
            shares = int(raw_shares)
            close = int(raw_close)
            previous_shares = self._last_shares.get(ticker)
            previous_close = self._last_close.get(ticker)

            is_jump = (
                previous_shares is not None
                and previous_shares > 0
                and abs(shares - previous_shares) / previous_shares > self._change_rate
            )
            if is_jump:
                # 등락률은 % 단위로 저장되므로 비율로 변환한다 (루트 CLAUDE.md 비율 표기 규칙)
                raw_rate = (
                    close / previous_close - 1 if previous_close is not None and previous_close > 0 else float("nan")
                )
                self._observations.setdefault(ticker, []).append(
                    ActionObservation(
                        target=target,
                        disclosed_rate=float(raw_change_rate) / PERCENT_TO_RATE,
                        raw_rate=raw_rate,
                    )
                )

            self._last_shares[ticker] = shares
            self._last_close[ticker] = close

    def observations_for(self, ticker: str) -> tuple[ActionObservation, ...]:
        """티커의 상장주식수 급변일 관측치를 반환한다.

        Args:
            ticker: 대상 티커

        Returns:
            급변일 관측치 (일자 오름차순, 없으면 빈 튜플)
        """
        return tuple(self._observations.get(ticker, ()))
