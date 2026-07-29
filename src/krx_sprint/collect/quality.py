"""1단 스냅샷 품질 검사

저장된 스냅샷 전체를 훑어 스펙 §8의 품질 항목을 판정한다.

수집기의 저장 직전 인라인 검증이 "이 일자 데이터가 형식상 말이 되는가"를 본다면,
이 모듈은 **일자 간·종목 간 정합성**(커버리지 결손, 중복 티커, 폐지 종목 소실,
티커 재사용, 휴장 오판)을 본다. 둘은 대체 관계가 아니다.

이 모듈은 데이터를 수정하지 않는다. 탐지·보고만 하고 조치는 사람이 판단한다.
"""

from collections.abc import Container
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

import pandas as pd

from krx_sprint.common_constants import (
    COL_CHANGE_RATE,
    COL_CLOSE,
    COL_HIGH,
    COL_LOW,
    COL_MARKET_CAP,
    COL_OPEN,
    COL_SHARES,
    COL_TICKER,
    COL_VOLUME,
    PRICE_LIMIT_RATE,
    SNAPSHOT_COLUMNS,
)

# 주말 시작 요일 (월=0 기준, 토=5)
WEEKEND_START_WEEKDAY = 5

# 티커 재사용 의심 판정 기준 (일). 이 기간 이상 사라졌다가 재등장하면 다른 회사일 수 있다 (스펙 §10.4)
TICKER_REUSE_GAP_DAYS = 180

# 폐지 판정 기준 (일). 마지막 수집일로부터 이 기간 이상 등장이 없으면 폐지로 본다
DELISTED_ABSENCE_DAYS = 30

# 거래정지 비율 경고 임계 (비율, 0.5 = 50%)
HALTED_RATIO_WARN = 0.5

# 상장주식수 급변 경고 임계 (비율, 0.10 = 10%).
# 증자·감자·액면분할·병합이 있은 날이며, 그날 등락률은 원본가 기준이라 왜곡된다 (스펙 §10.5)
SHARES_CHANGE_WARN_RATE = 0.10

# 이슈 상세에 담을 위반 종목 샘플 최대 개수
SAMPLE_LIMIT = 5


class Severity(Enum):
    """이슈 심각도

    ERROR는 데이터를 신뢰할 수 없어 조치가 필요한 경우,
    WARNING은 정상일 수 있으나 확인이 필요한 경우,
    INFO는 판단 근거가 되는 집계값이다.
    """

    ERROR = "오류"
    WARNING = "경고"
    INFO = "정보"


@dataclass(frozen=True)
class QualityIssue:
    """품질 검사에서 발견한 항목

    Attributes:
        severity: 심각도
        category: 검사 항목 이름
        target: 대상 (일자 또는 티커)
        detail: 판정 근거
    """

    severity: Severity
    category: str
    target: str
    detail: str


@dataclass(frozen=True)
class AnchorRecord:
    """외부에서 확인된 알려진 값 (스펙 §3.1)

    내부 일관성만으로는 "통째로 틀린" 데이터를 잡을 수 없으므로,
    검증된 실제 값과 대조한다.

    Attributes:
        target: 대상 일자
        ticker: 대상 티커
        close: 종가
        shares: 상장주식수
        market_cap: 시가총액
    """

    target: date
    ticker: str
    close: int
    shares: int
    market_cap: int


# 스펙 §3.1에 기록된 검증 완료 값.
# 외부 시세와 대조해 확인한 값이 늘어나면 여기에 추가한다.
ANCHORS = (
    AnchorRecord(
        target=date(2021, 1, 4),
        ticker="005930",
        close=83000,
        shares=5969782550,
        market_cap=495491951650000,
    ),
    # 외부 시세(Yahoo Finance)와 OHLC·거래량까지 완전 일치를 확인한 구간
    AnchorRecord(
        target=date(2026, 7, 24),
        ticker="005930",
        close=249500,
        shares=5846278608,
        market_cap=1458646512696000,
    ),
    AnchorRecord(
        target=date(2026, 7, 27),
        ticker="005930",
        close=254000,
        shares=5846278608,
        market_cap=1484954766432000,
    ),
    # 액면분할(5:1) 직후. 분할 후 주식수가 DART 공시(5,090,828 → 25,454,140)와 일치함을 확인
    AnchorRecord(
        target=date(2026, 7, 27),
        ticker="025560",
        close=10210,
        shares=25454140,
        market_cap=259886769400,
    ),
)


def _require_schema(snapshot: pd.DataFrame) -> None:
    """스냅샷 스키마를 확인한다.

    Args:
        snapshot: 검사 대상 스냅샷

    Raises:
        ValueError: 필요한 컬럼이 없는 경우
    """
    missing = [column for column in SNAPSHOT_COLUMNS if column not in snapshot.columns]
    if missing:
        raise ValueError(f"스냅샷에 필요한 컬럼이 없습니다: {missing}")


def check_coverage(
    start: date,
    end: date,
    collected: Container[date],
    holidays: Container[date],
) -> tuple[QualityIssue, ...]:
    """기간 내 평일이 모두 수집 또는 휴장으로 설명되는지 검사한다.

    어느 쪽에도 없는 평일은 조회가 누락된 것이다.

    Args:
        start: 검사 시작 일자
        end: 검사 종료 일자 (포함)
        collected: 수집 완료 일자
        holidays: 휴장으로 기록된 일자

    Returns:
        결손 일자별 이슈
    """
    issues: list[QualityIssue] = []
    current = start

    while current <= end:
        is_weekday = current.weekday() < WEEKEND_START_WEEKDAY
        if is_weekday and current not in collected and current not in holidays:
            issues.append(
                QualityIssue(
                    severity=Severity.ERROR,
                    category="커버리지 결손",
                    target=current.isoformat(),
                    detail="수집·휴장 어디에도 기록되지 않은 평일입니다",
                )
            )
        current += timedelta(days=1)

    return tuple(issues)


def _check_duplicates(snapshot: pd.DataFrame, target: date) -> list[QualityIssue]:
    """같은 일자에 중복 등장한 티커를 찾는다."""
    duplicated = snapshot[COL_TICKER].duplicated(keep=False)
    if not bool(duplicated.any()):
        return []

    sample = sorted(set(snapshot.loc[duplicated, COL_TICKER]))[:SAMPLE_LIMIT]
    return [
        QualityIssue(
            severity=Severity.ERROR,
            category="중복 티커",
            target=target.isoformat(),
            detail=f"{int(duplicated.sum())}행 중복 (예: {sample})",
        )
    ]


def _check_prices(snapshot: pd.DataFrame, target: date) -> list[QualityIssue]:
    """가격 정합성을 검사한다 (음수·거래 중 종가 없음·고가 < 저가·정규장 미형성)."""
    issues: list[QualityIssue] = []
    prices = snapshot[[COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE]]

    negative = prices.lt(0).any(axis=1)
    if bool(negative.any()):
        sample = snapshot.loc[negative, COL_TICKER].head(SAMPLE_LIMIT).tolist()
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                category="가격 정합성",
                target=target.isoformat(),
                detail=f"음수 가격 {int(negative.sum())}종목 (예: {sample})",
            )
        )

    traded = snapshot[COL_VOLUME] > 0

    invalid_close = traded & (snapshot[COL_CLOSE] <= 0)
    if bool(invalid_close.any()):
        sample = snapshot.loc[invalid_close, COL_TICKER].head(SAMPLE_LIMIT).tolist()
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                category="가격 정합성",
                target=target.isoformat(),
                detail=f"거래가 있는데 종가 0 이하 {int(invalid_close.sum())}종목 (예: {sample})",
            )
        )

    high_low = snapshot[COL_HIGH] < snapshot[COL_LOW]
    if bool(high_low.any()):
        sample = snapshot.loc[high_low, COL_TICKER].head(SAMPLE_LIMIT).tolist()
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                category="가격 정합성",
                target=target.isoformat(),
                detail=f"고가 < 저가 {int(high_low.sum())}종목 (예: {sample})",
            )
        )

    # 거래는 있었지만 정규장 가격이 형성되지 않은 행 (시간외 체결 등).
    # 이상치는 아니지만 고가·저가가 0이라 스윙·전저점 계산에 쓰면 가짜 저점이 생긴다.
    no_regular_session = traded & (snapshot[COL_LOW] <= 0)
    if bool(no_regular_session.any()):
        sample = snapshot.loc[no_regular_session, COL_TICKER].head(SAMPLE_LIMIT).tolist()
        issues.append(
            QualityIssue(
                severity=Severity.WARNING,
                category="정규장 미형성",
                target=target.isoformat(),
                detail=f"거래는 있으나 고가·저가 0 {int(no_regular_session.sum())}종목 (예: {sample})",
            )
        )

    return issues


def _check_market_cap(snapshot: pd.DataFrame, target: date) -> list[QualityIssue]:
    """시총 검산(종가 × 상장주식수 = 시가총액)을 검사한다."""
    expected = snapshot[COL_CLOSE] * snapshot[COL_SHARES]
    mismatch = expected != snapshot[COL_MARKET_CAP]
    if not bool(mismatch.any()):
        return []

    sample = snapshot.loc[mismatch, COL_TICKER].head(SAMPLE_LIMIT).tolist()
    return [
        QualityIssue(
            severity=Severity.ERROR,
            category="시총 검산",
            target=target.isoformat(),
            detail=f"종가 × 상장주식수 ≠ 시가총액 {int(mismatch.sum())}종목 (예: {sample})",
        )
    ]


def _check_anchors(snapshot: pd.DataFrame, target: date) -> list[QualityIssue]:
    """해당 일자에 앵커가 있으면 알려진 값과 대조한다."""
    issues: list[QualityIssue] = []

    for anchor in ANCHORS:
        if anchor.target != target:
            continue

        rows = snapshot[snapshot[COL_TICKER] == anchor.ticker]
        if rows.empty:
            issues.append(
                QualityIssue(
                    severity=Severity.ERROR,
                    category="앵커 검산",
                    target=anchor.ticker,
                    detail=f"{target.isoformat()} 스냅샷에 앵커 종목이 없습니다",
                )
            )
            continue

        row = rows.iloc[0]
        actual = (int(row[COL_CLOSE]), int(row[COL_SHARES]), int(row[COL_MARKET_CAP]))
        expected = (anchor.close, anchor.shares, anchor.market_cap)
        if actual != expected:
            issues.append(
                QualityIssue(
                    severity=Severity.ERROR,
                    category="앵커 검산",
                    target=anchor.ticker,
                    detail=f"{target.isoformat()} 기대 (종가/주식수/시총)={expected}, 실제={actual}",
                )
            )

    return issues


def check_daily_snapshot(snapshot: pd.DataFrame, target: date) -> tuple[QualityIssue, ...]:
    """한 일자 스냅샷의 품질을 검사한다.

    Args:
        snapshot: 검사 대상 스냅샷
        target: 해당 일자

    Returns:
        발견된 이슈 (없으면 빈 튜플)

    Raises:
        ValueError: 스키마가 규칙과 다른 경우
    """
    # 1. 구조 확인
    _require_schema(snapshot)

    if snapshot.empty:
        return (
            QualityIssue(
                severity=Severity.ERROR,
                category="빈 스냅샷",
                target=target.isoformat(),
                detail="행이 없는 스냅샷 파일입니다",
            ),
        )

    # 2. 거래정지 패턴 식별 (거래량 0 + 시가·고가·저가 0)
    halted = (
        (snapshot[COL_VOLUME] == 0) & (snapshot[COL_OPEN] == 0) & (snapshot[COL_HIGH] == 0) & (snapshot[COL_LOW] == 0)
    )

    issues: list[QualityIssue] = []

    # 3. 전 종목 가격 0 = 휴장일이 거래일로 저장된 것 (시범 수집에서 실제 발생)
    all_prices_zero = bool((snapshot[[COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE]] == 0).all(axis=None))
    if all_prices_zero:
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                category="휴장 오판",
                target=target.isoformat(),
                detail=f"전 종목({len(snapshot)})의 가격이 0입니다. 휴장일이 거래일로 저장됐습니다",
            )
        )
        return tuple(issues)

    # 4. 개별 검사
    issues.extend(_check_duplicates(snapshot, target))
    issues.extend(_check_prices(snapshot, target))
    issues.extend(_check_market_cap(snapshot, target))
    issues.extend(_check_anchors(snapshot, target))

    # 5. 거래정지 비율이 과도하면 확인 대상
    halted_ratio = float(halted.mean())
    if halted_ratio > HALTED_RATIO_WARN:
        issues.append(
            QualityIssue(
                severity=Severity.WARNING,
                category="거래정지 급증",
                target=target.isoformat(),
                detail=f"거래정지 비율 {halted_ratio:.4f} ({int(halted.sum())}/{len(snapshot)}종목)",
            )
        )

    # 6. 가격제한폭 초과 등락률은 권리락 등 정상 사례일 수 있어 경고로만 남긴다
    limit_pct = PRICE_LIMIT_RATE * 100
    beyond_limit = snapshot[COL_CHANGE_RATE].abs() > limit_pct
    if bool(beyond_limit.any()):
        sample = snapshot.loc[beyond_limit, COL_TICKER].head(SAMPLE_LIMIT).tolist()
        issues.append(
            QualityIssue(
                severity=Severity.WARNING,
                category="등락률 초과",
                target=target.isoformat(),
                detail=f"가격제한폭({limit_pct:.0f}%) 초과 {int(beyond_limit.sum())}종목 (예: {sample})",
            )
        )

    return tuple(issues)


class TickerTracker:
    """티커별 등장 이력과 상장주식수 변화를 스트리밍으로 추적한다.

    전체 데이터를 메모리에 올리지 않기 위해 티커별 **직전 등장 일자와 직전 상장주식수**만
    유지한다. 일자를 오름차순으로 `observe`에 넘긴 뒤 `finalize`를 호출한다.
    """

    def __init__(
        self,
        reuse_gap_days: int = TICKER_REUSE_GAP_DAYS,
        shares_change_rate: float = SHARES_CHANGE_WARN_RATE,
    ) -> None:
        """추적기를 초기화한다.

        Args:
            reuse_gap_days: 재사용 의심으로 판정할 공백 일수
            shares_change_rate: 상장주식수 급변으로 판정할 변동 비율
        """
        self._reuse_gap_days = reuse_gap_days
        self._shares_change_rate = shares_change_rate
        self._last_seen: dict[str, date] = {}
        self._last_shares: dict[str, int] = {}

    def observe(self, target: date, snapshot: pd.DataFrame) -> tuple[QualityIssue, ...]:
        """한 일자 스냅샷을 반영하고 티커 재사용·상장주식수 급변을 판정한다.

        Args:
            target: 관측 일자
            snapshot: 해당 일자 스냅샷

        Returns:
            재사용 의심·상장주식수 급변 이슈
        """
        issues: list[QualityIssue] = []

        for ticker, shares in zip(snapshot[COL_TICKER], snapshot[COL_SHARES], strict=True):
            previous = self._last_seen.get(ticker)

            if previous is not None:
                gap_days = (target - previous).days
                if gap_days > self._reuse_gap_days:
                    # 다른 회사일 수 있으므로 상장주식수는 비교하지 않는다
                    issues.append(
                        QualityIssue(
                            severity=Severity.WARNING,
                            category="티커 재사용",
                            target=ticker,
                            detail=f"{previous.isoformat()} 이후 {gap_days}일 만에 재등장 ({target.isoformat()})",
                        )
                    )
                else:
                    issues.extend(self._check_shares_change(ticker, target, int(shares)))

            self._last_seen[ticker] = target
            self._last_shares[ticker] = int(shares)

        return tuple(issues)

    def _check_shares_change(self, ticker: str, target: date, shares: int) -> list[QualityIssue]:
        """직전 등장일 대비 상장주식수 급변을 판정한다.

        증자·감자·액면분할·병합이 있은 날이며, 그날 등락률은 원본가 기준이라 왜곡된다.
        기준봉 판정에서 제외해야 하는 일자를 식별하는 것이 목적이다 (스펙 §10.5).

        Args:
            ticker: 대상 티커
            target: 관측 일자
            shares: 해당 일자 상장주식수

        Returns:
            급변 이슈 (임계 이하면 빈 목록)
        """
        previous_shares = self._last_shares.get(ticker)
        if previous_shares is None or previous_shares <= 0:
            return []

        change_ratio = abs(shares - previous_shares) / previous_shares
        if change_ratio <= self._shares_change_rate:
            return []

        return [
            QualityIssue(
                severity=Severity.WARNING,
                category="상장주식수 급변",
                target=ticker,
                detail=(
                    f"{target.isoformat()} 상장주식수 {previous_shares:,} → {shares:,} "
                    f"(변동률 {change_ratio:.4f}). 해당 일자 등락률은 원본가 기준이라 왜곡될 수 있습니다"
                ),
            )
        ]

    def finalize(self, last_date: date) -> tuple[QualityIssue, ...]:
        """관측을 마치고 폐지로 볼 티커를 보고한다.

        폐지 종목이 데이터에 남아 있는 것은 생존편향 방지가 동작했다는 뜻이므로
        오류가 아니라 정보로 보고한다 (스펙 §3.2).

        Args:
            last_date: 수집 구간의 마지막 일자

        Returns:
            폐지로 판정된 티커별 이슈
        """
        issues: list[QualityIssue] = []

        for ticker, seen in sorted(self._last_seen.items()):
            absence_days = (last_date - seen).days
            if absence_days >= DELISTED_ABSENCE_DAYS:
                issues.append(
                    QualityIssue(
                        severity=Severity.INFO,
                        category="폐지 종목",
                        target=ticker,
                        detail=f"마지막 등장 {seen.isoformat()} (이후 {absence_days}일 부재)",
                    )
                )

        return tuple(issues)
