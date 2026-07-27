"""데이터 로딩 및 검증 유틸리티

프로젝트 전반에서 사용되는 공통 CSV 파일 로딩 함수를 제공한다.

학습 포인트:
1. pandas: 데이터 분석을 위한 핵심 라이브러리 (DataFrame 제공)
2. 중앙 집중식 데이터 로딩: 공통 CSV 로딩 로직을 한 곳에서 관리
3. 체이닝: df.sort_values().reset_index() 처럼 메서드를 연결해서 사용

참고:
- TQQQ 도메인 전용 데이터 로더: tqqq/data_loader.py 참고
"""

from pathlib import Path

import pandas as pd

from qbt.common_constants import COL_DATE, REQUIRED_COLUMNS
from qbt.utils.logger import get_logger

# 모듈 레벨 로거 생성
# __name__: 현재 모듈의 이름 (예: "qbt.utils.data_loader")
logger = get_logger(__name__)


def load_stock_data(path: Path) -> pd.DataFrame:
    """
    CSV 파일에서 주식 데이터를 로드하고 전처리한다.

    학습 포인트:
    1. pd.DataFrame: pandas의 핵심 자료구조 (행과 열로 구성된 테이블)
    2. set 연산: 집합 차집합으로 누락된 컬럼 찾기
    3. 메서드 체이닝: .sort_values().reset_index() - 여러 작업을 연결
    4. raise: 예외를 발생시켜 오류 상황을 호출자에게 알림

    날짜 파싱, 정렬, 필수 컬럼 검증, 중복 제거를 수행한다.

    Args:
        path: CSV 파일 경로

    Returns:
        전처리된 DataFrame (날짜순 정렬됨)

    Raises:
        FileNotFoundError: 파일이 존재하지 않을 때
        ValueError: 필수 컬럼이 누락되었을 때
    """
    # 1. 파일 존재 여부 확인
    # path.exists(): Path 객체가 가리키는 파일/디렉토리가 존재하는지 확인
    if not path.exists():
        # raise: 예외를 발생시킴 (함수 실행을 중단하고 오류를 전파)
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")

    # f-string 사용: f"{변수}" 형태로 문자열에 변수 삽입
    logger.debug(f"데이터 로딩 시작: {path}")

    # 2. CSV 파일 로드
    # pd.read_csv(): CSV 파일을 읽어 DataFrame으로 변환
    # DataFrame: 행(row)과 열(column)로 구성된 2차원 테이블
    # parse_dates: CSV 읽기 시점에 날짜 컬럼을 자동으로 파싱 (성능 향상)
    df = pd.read_csv(path, parse_dates=[COL_DATE])

    # len(df): DataFrame의 행(row) 개수
    # :,는 천 단위 구분자 (예: 1000 → 1,000)
    logger.debug(f"원본 데이터 행 수: {len(df):,}")

    # 3. 필수 컬럼 검증
    # set(): 집합(Set) 생성 - 중복 없이 요소 저장, 집합 연산 지원
    # set(A) - set(B): 차집합 - A에는 있지만 B에는 없는 요소
    # df.columns: DataFrame의 모든 컬럼명 리스트
    missing_columns = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing_columns:
        # sorted(): 리스트를 정렬 (오름차순)
        raise ValueError(f"필수 컬럼이 누락되었습니다: {sorted(missing_columns)}")

    # 4. 날짜 컬럼 파싱
    # pd.to_datetime(): 문자열을 datetime 객체로 변환
    # .dt.date: datetime에서 날짜 부분만 추출 (시간 제거)
    # df[COL_DATE]: DataFrame에서 특정 컬럼 접근 (엑셀의 열과 유사)
    df[COL_DATE] = pd.to_datetime(df[COL_DATE]).dt.date

    # 5. 날짜순 정렬
    # .sort_values(): 특정 컬럼 기준으로 정렬
    # .reset_index(drop=True): 인덱스 재설정 (기존 인덱스는 버림)
    # 메서드 체이닝: 여러 메서드를 .으로 연결해서 호출
    df = df.sort_values(COL_DATE).reset_index(drop=True)

    # 6. 중복 날짜 제거 (첫 번째 값 유지)
    # .duplicated(): 중복된 행 찾기 (True/False 반환)
    # subset: 특정 컬럼만 기준으로 중복 체크
    # .sum(): True=1, False=0으로 계산하여 합계 (중복 개수)
    duplicate_count = df.duplicated(subset=[COL_DATE]).sum()
    if duplicate_count > 0:
        logger.warning(f"중복 날짜 {duplicate_count}건 제거됨")
        # .drop_duplicates(): 중복 행 제거
        # keep="first": 첫 번째 행만 유지
        df = df.drop_duplicates(subset=[COL_DATE], keep="first").reset_index(drop=True)

    # df[COL_DATE].min/max(): 컬럼의 최소/최대값
    logger.debug(f"전처리 완료: {len(df):,}행, 기간 {df[COL_DATE].min()} ~ {df[COL_DATE].max()}")

    return df


def extract_overlap_period(
    df1: pd.DataFrame,
    df2: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    두 DataFrame의 겹치는 기간을 추출한다.

    Args:
        df1: 첫 번째 DataFrame (Date 컬럼 필수)
        df2: 두 번째 DataFrame (Date 컬럼 필수)

    Returns:
        (df1_overlap, df2_overlap) 튜플
            - df1_overlap: 겹치는 기간의 df1 (날짜순 정렬, reset_index 완료)
            - df2_overlap: 겹치는 기간의 df2 (날짜순 정렬, reset_index 완료)

    Raises:
        ValueError: 겹치는 기간이 없을 때
    """
    # 1. 겹치는 날짜 추출 (set 교집합)
    dates1 = set(df1[COL_DATE])
    dates2 = set(df2[COL_DATE])
    overlap_dates_set = dates1 & dates2

    if not overlap_dates_set:
        raise ValueError("두 DataFrame 간 겹치는 기간이 없습니다")

    # 2. 날짜순 정렬
    overlap_dates = sorted(overlap_dates_set)

    # 3. 겹치는 기간 데이터 추출
    df1_overlap = df1[df1[COL_DATE].isin(overlap_dates)].sort_values(COL_DATE).reset_index(drop=True)
    df2_overlap = df2[df2[COL_DATE].isin(overlap_dates)].sort_values(COL_DATE).reset_index(drop=True)

    return df1_overlap, df2_overlap


def load_signal_trade_pair(
    signal_path: Path,
    trade_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """시그널 + 매매 데이터를 로드하고, 경로가 다르면 겹치는 기간을 추출한다.

    signal_path == trade_path인 경우 한 번만 로드하고 독립 복사본을 반환한다.
    경로가 다르면 각각 로드한 뒤 extract_overlap_period로 겹치는 기간만 추출한다.

    Args:
        signal_path: 시그널 데이터 CSV 경로
        trade_path: 매매 데이터 CSV 경로

    Returns:
        (signal_df, trade_df) 튜플. 두 DataFrame은 독립 복사본.

    Raises:
        FileNotFoundError: 파일이 존재하지 않을 때
        ValueError: 필수 컬럼 누락 또는 겹치는 기간 없을 때
    """
    if signal_path == trade_path:
        trade_df = load_stock_data(trade_path)
        signal_df = trade_df.copy()
    else:
        signal_df = load_stock_data(signal_path)
        trade_df = load_stock_data(trade_path)
        signal_df, trade_df = extract_overlap_period(signal_df, trade_df)

    return signal_df, trade_df
