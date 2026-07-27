"""krx-sprint 공통 상수

경로 상수와 데이터 스키마 상수를 단일 관리한다.
저장 구조의 근거는 docs/데이터수집_스펙_v2.md §7 참고.
"""

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
