"""
실행 메타데이터 관리 모듈

CSV 결과 파일의 메타데이터를 JSON으로 관리하고,
최근 N개 이력을 순환 저장한다.

학습 포인트:
1. JSON: JavaScript Object Notation - 데이터를 텍스트로 저장하는 형식
2. 타입 별칭(Type Alias): 복잡한 타입에 이름 붙이기
3. with 문: 파일을 자동으로 닫아주는 컨텍스트 관리자
4. .copy(): 딕셔너리 복사본 생성 (원본 보호)
"""

import json
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from krx_sprint.common_constants import META_JSON_PATH

# 메타데이터 관리 상수
# meta.json에 저장할 최대 실행 이력 개수
# 오래된 이력은 자동으로 삭제되어 파일 크기 관리
MAX_HISTORY_COUNT = 5

# 타입 별칭(Type Alias): 복잡한 타입에 짧은 이름 부여
# MetaDict: 문자열 키와 임의 값을 가진 딕셔너리
MetaDict = dict[str, Any]

# HistoryList: MetaDict의 리스트
HistoryList = list[MetaDict]

# FullMetaJson: 문자열 키와 HistoryList 값을 가진 딕셔너리
FullMetaJson = dict[str, HistoryList]


def _load_full_metadata() -> FullMetaJson:
    """
    meta.json 전체를 로드한다.

    학습 포인트:
    1. with 문: 파일을 자동으로 닫아주는 안전한 파일 처리
    2. encoding="utf-8": 한글 등 유니코드 문자 처리
    3. json.load(): JSON 텍스트를 Python 객체(dict, list 등)로 변환

    Returns:
        전체 메타데이터 dict (파일 없으면 빈 dict)
    """
    # 파일 없으면 빈 dict 반환
    if not META_JSON_PATH.exists():
        # {} : 빈 딕셔너리 (키-값 쌍이 없음)
        return {}

    # JSON 파싱
    # with 문: 블록이 끝나면 자동으로 파일을 닫음 (finally 불필요)
    # .open("r", ...): 읽기 모드로 파일 열기 ("w"는 쓰기, "a"는 추가)
    # encoding="utf-8": 한글 등 모든 문자를 올바르게 읽기
    # as f: 파일 객체를 f 변수에 저장
    with META_JSON_PATH.open("r", encoding="utf-8") as f:
        # json.load(파일): JSON 파일을 읽어 Python 객체로 변환
        # JSON의 {} → dict, [] → list, "문자열" → str, 123 → int 등
        full_meta: FullMetaJson = json.load(f)

    return full_meta


def _rotate_history(history: HistoryList, new_entry: MetaDict) -> HistoryList:
    """
    이력 리스트에 새 항목을 추가하고 최대 개수를 유지한다.

    Args:
        history: 기존 이력 리스트
        new_entry: 새로 추가할 항목

    Returns:
        업데이트된 이력 (최대 MAX_HISTORY_COUNT개, 최신순 정렬)
    """
    # 맨 앞에 새 항목 추가
    updated = [new_entry] + history

    # 최대 개수만 유지
    return updated[:MAX_HISTORY_COUNT]


def _add_timestamp(metadata: MetaDict) -> MetaDict:
    """
    메타데이터에 KST(Asia/Seoul) 타임존의 ISO 8601 타임스탬프를 추가한다.

    Args:
        metadata: 원본 메타데이터

    Returns:
        타임스탬프가 추가된 메타데이터 (원본 변경 없음)
    """
    # 원본 변경 방지 (복사)
    result = metadata.copy()

    # KST 타임스탬프 추가 (명시적으로 Asia/Seoul 타임존 지정)
    now = datetime.now(UTC).astimezone(ZoneInfo("Asia/Seoul"))
    result["timestamp"] = now.isoformat(timespec="seconds")

    return result


def save_metadata(csv_type: str, metadata: MetaDict) -> None:
    """
    메타데이터를 meta.json에 저장한다.

    최근 N개 이력만 유지하는 순환 저장 방식을 사용한다.
    타임스탬프는 자동으로 추가된다.

    Args:
        csv_type: CSV 타입 식별자
        metadata: 저장할 메타데이터 (타임스탬프 자동 추가)
    """
    # 1. 기존 meta.json 로드
    full_meta = _load_full_metadata()

    # 2. 타임스탬프 추가
    metadata_with_ts = _add_timestamp(metadata)

    # 3. 해당 타입의 이력 가져오기
    history: HistoryList = full_meta.get(csv_type, [])

    # 4. 최근 N개만 유지
    updated_history = _rotate_history(history, metadata_with_ts)

    # 5. 전체 dict 업데이트
    full_meta[csv_type] = updated_history

    # 6. meta.json 저장
    META_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with META_JSON_PATH.open("w", encoding="utf-8") as f:
        json.dump(full_meta, f, indent=2, ensure_ascii=False)
