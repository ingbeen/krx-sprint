"""krx-sprint 공통 상수

경로 상수와 데이터 스키마 상수를 단일 관리한다.
저장 구조의 근거는 docs/데이터수집_스펙_v2.md §7 참고.
"""

import re
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

# ============================================================
# 경로 상수
# ============================================================

# 프로젝트 루트 (src/krx_sprint/common_constants.py 기준 2단계 상위)
BASE_DIR = Path(__file__).resolve().parents[2]

# 데이터 저장소
STORAGE_DIR = BASE_DIR / "storage"
SNAPSHOTS_DIR = STORAGE_DIR / "snapshots"  # 1단: 일별 전종목 스냅샷 (원본가, 일자별 불변 parquet)
ADJUSTED_DIR = STORAGE_DIR / "adjusted"  # 2단: 종목별 수정주가 시계열 parquet
META_DIR = STORAGE_DIR / "meta"  # 수집 메타데이터 (JSON)
CACHE_DIR = STORAGE_DIR / "cache"  # 파생 캐시 (git 제외, 언제든 재생성 가능)
PANEL_DIR = CACHE_DIR / "panel"  # 백테스트 통합 패널 (1·2단 조인 결과, 연도별 parquet)
BACKTEST_DIR = STORAGE_DIR / "backtest"  # 백테스트 실행 결과 (git 제외, 재생성 가능)

# 자격증명 파일 (git 제외). KRX 로그인 정보의 단일 출처
ENV_FILE_PATH = BASE_DIR / ".env"

# 메타 파일
NAMES_CSV_PATH = STORAGE_DIR / "names.csv"  # 티커 → 종목명 매핑 (수집 시점 축적)
META_JSON_PATH = META_DIR / "meta.json"  # 실행 이력 (meta_manager)
COLLECTION_META_PATH = META_DIR / "collection_meta.json"  # pykrx 버전·최종 수집 일시·수집 범위
FAILURES_JSON_PATH = META_DIR / "failures.json"  # 실패 일자/종목 목록 (재수집 대상)
HOLIDAYS_JSON_PATH = META_DIR / "holidays.json"  # 휴장 판정 일자 (재조회 방지)

# ============================================================
# 수집 상수
# ============================================================

# 수집 시작일 (2019~: 횡보장·폭락·버블·하락장 표본 포함, 스펙 §5)
COLLECTION_START_DATE = date(2019, 1, 1)

# 수집 대상 시장 (코넥스 제외, 스펙 §5)
MARKETS = ("KOSPI", "KOSDAQ")

# 프로젝트 기준 타임존
KST = ZoneInfo("Asia/Seoul")

# 당일 스냅샷 확정 판정 시각 (KST, 0~23)
# 조회가 성공해도 장중 미확정 값일 수 있으므로(스펙 §0 실측) 이 시각 이전에는 당일을 수집하지 않는다
SNAPSHOT_CONFIRM_HOUR_KST = 17

# 요청 간 지연 (초, 스펙 §9 레이트리밋)
REQUEST_DELAY_SECONDS = 1.0

# 조회 실패 시 최대 시도 횟수 (최초 1회 + 재시도)
MAX_ATTEMPT_COUNT = 3

# 지수 백오프 기본 대기 (초). n번째 재시도 대기 = RETRY_BACKOFF_BASE_SECONDS * 2 ** (n - 1)
RETRY_BACKOFF_BASE_SECONDS = 2.0

# 가격제한폭 비율 (0.30 = 30%). 등락률이 이를 넘으면 권리락 등 특이일로 보고 경고한다 (스펙 §8)
PRICE_LIMIT_RATE = 0.30

# ============================================================
# 스냅샷 컬럼 상수 (내부 계산용 영문 토큰)
# ============================================================

# 티커 형식: 6자리 영숫자(숫자 또는 대문자 영문).
# 숫자 전용이 아니다 — 신형 우선주(예: 03473K)와 2025-07 이후 신규 종목코드(예: 0001A0)가
# 영문을 포함한다. 수집된 스냅샷 3,135종목 중 78종목이 이 형태다 (실측, 스펙 §7.2)
TICKER_PATTERN = re.compile(r"^[0-9A-Z]{6}$")

COL_DATE = "date"
COL_TICKER = "ticker"
COL_MARKET = "market"
COL_OPEN = "open"
COL_HIGH = "high"
COL_LOW = "low"
COL_CLOSE = "close"
COL_VOLUME = "volume"
COL_VALUE = "value"  # 거래대금(원)
COL_CHANGE_RATE = "change_rate"  # 등락률 (pykrx 반환값 그대로, % 단위)
COL_MARKET_CAP = "market_cap"  # 시가총액(원)
COL_SHARES = "shares"  # 상장주식수

# 1단 스냅샷 저장 컬럼 순서 (스펙 §7.2)
SNAPSHOT_COLUMNS = [
    COL_TICKER,
    COL_MARKET,
    COL_OPEN,
    COL_HIGH,
    COL_LOW,
    COL_CLOSE,
    COL_VOLUME,
    COL_VALUE,
    COL_CHANGE_RATE,
    COL_MARKET_CAP,
    COL_SHARES,
]

# 2단 수정주가 저장 컬럼 순서 (스펙 §7.2)
# 원본가와 혼용하면 신호가 오염되므로 폴더와 컬럼 목록을 모두 분리한다 (스펙 §10.3)
ADJUSTED_COLUMNS = [
    COL_DATE,
    COL_OPEN,
    COL_HIGH,
    COL_LOW,
    COL_CLOSE,
    COL_VOLUME,
]

# 2단 가격 컬럼. 수정주가는 분할·증자 조정으로 소수가 될 수 있어 float64로 저장한다
ADJUSTED_PRICE_COLUMNS = [COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE]

# ============================================================
# 통합 패널 컬럼 상수 (백테스트 설계 §2.3)
# ============================================================

# 패널에 실리는 수정주가 컬럼. `adj_` 접두사로 원본가 축과 구분한다 (스펙 §10.3 혼용 차단)
COL_ADJ_OPEN = "adj_open"
COL_ADJ_HIGH = "adj_high"
COL_ADJ_LOW = "adj_low"
COL_ADJ_CLOSE = "adj_close"

# 처리 규칙 플래그. 수집 단계에서 확정된 규칙을 데이터에 박아 소비자가 빠뜨릴 수 없게 한다
COL_IS_HALTED = "is_halted"  # 거래정지 → 매매 불가
COL_NO_REGULAR_SESSION = "no_regular_session"  # 정규장 미형성 → 고저 계산 제외
COL_IS_SHARES_JUMP = "is_shares_jump"  # 상장주식수 급변 → 기준봉·신규 진입 제외
COL_IS_UNADJUSTED_ACTION = "is_unadjusted_action"  # 수정 미반영 액션 → 수익률·스윙 제외
COL_IS_LIMIT_UP_CLOSE = "is_limit_up_close"  # 상한가 마감 → 매수 미체결
COL_IS_LIMIT_DOWN_CLOSE = "is_limit_down_close"  # 하한가 마감 → 매도 미체결
COL_IS_LAST_SEEN = "is_last_seen"  # 1단 최종 등장일 → 폐지 청산 트리거

PANEL_ADJUSTED_COLUMNS = [COL_ADJ_OPEN, COL_ADJ_HIGH, COL_ADJ_LOW, COL_ADJ_CLOSE]

PANEL_FLAG_COLUMNS = [
    COL_IS_HALTED,
    COL_NO_REGULAR_SESSION,
    COL_IS_SHARES_JUMP,
    COL_IS_UNADJUSTED_ACTION,
    COL_IS_LIMIT_UP_CLOSE,
    COL_IS_LIMIT_DOWN_CLOSE,
    COL_IS_LAST_SEEN,
]

# 통합 패널 저장 컬럼 순서.
# 2단 거래량은 조정돼 있어(1,147종목) 거래대금·유동성 계산에 쓰면 신호가 오염되므로
# 컬럼 자체를 만들지 않는다 — 없는 컬럼은 잘못 쓸 수 없다 (설계 §2.3)
PANEL_COLUMNS = [
    COL_DATE,
    COL_TICKER,
    COL_MARKET,
    COL_OPEN,
    COL_HIGH,
    COL_LOW,
    COL_CLOSE,
    COL_VOLUME,
    COL_VALUE,
    COL_CHANGE_RATE,
    COL_MARKET_CAP,
    COL_SHARES,
    *PANEL_ADJUSTED_COLUMNS,
    *PANEL_FLAG_COLUMNS,
]
