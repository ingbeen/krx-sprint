"""meta_manager 스모크 테스트

실행 메타데이터 JSON의 순환 저장 계약과 입력 불변성을 고정한다.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from freezegun import freeze_time

from krx_sprint.utils import meta_manager
from krx_sprint.utils.meta_manager import MAX_HISTORY_COUNT, save_metadata

# 테스트에서 사용할 임의의 결과 타입 식별자
CSV_TYPE = "snapshot"
OTHER_CSV_TYPE = "adjusted"


@pytest.fixture
def meta_json_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """meta.json 경로를 tmp_path로 격리한다.

    meta_manager는 import 시점에 META_JSON_PATH를 캡처하므로 모듈 속성을 직접 패치한다.

    Returns:
        격리된 meta.json 경로 (상위 디렉토리는 아직 존재하지 않음)
    """
    path = tmp_path / "meta" / "meta.json"
    monkeypatch.setattr(meta_manager, "META_JSON_PATH", path)
    return path


def _load_history(meta_json_path: Path, csv_type: str) -> list[dict[str, Any]]:
    """저장된 meta.json에서 특정 타입의 이력을 읽는다."""
    with meta_json_path.open("r", encoding="utf-8") as f:
        full_meta: dict[str, list[dict[str, Any]]] = json.load(f)
    return full_meta[csv_type]


class TestSaveMetadata:
    """save_metadata의 저장 계약을 고정한다."""

    @freeze_time("2026-07-27T05:30:00Z")
    def test_creates_file_with_kst_timestamp(self, meta_json_path: Path):
        """
        목적: 첫 저장 시 상위 디렉토리를 만들고 KST ISO 8601 타임스탬프를 붙인다.

        Given: meta.json도 상위 디렉토리도 없는 상태 (UTC 05:30 고정)
        When: save_metadata를 1회 호출
        Then: 파일이 생성되고 timestamp가 KST(+09:00) 값으로 기록된다
        """
        # Given / When
        save_metadata(CSV_TYPE, {"rows": 2700})

        # Then
        history = _load_history(meta_json_path, CSV_TYPE)
        assert len(history) == 1
        assert history[0]["rows"] == 2700
        assert history[0]["timestamp"] == "2026-07-27T14:30:00+09:00"

    def test_rotates_history_to_max_count(self, meta_json_path: Path):
        """
        목적: 이력이 MAX_HISTORY_COUNT를 넘지 않고 최신순으로 유지된다.

        Given: 최대 보관 개수보다 2건 많은 저장 요청
        When: 순차적으로 save_metadata 호출
        Then: 최신 MAX_HISTORY_COUNT건만 최신순으로 남는다
        """
        # Given
        total_count = MAX_HISTORY_COUNT + 2

        # When
        for seq in range(total_count):
            save_metadata(CSV_TYPE, {"seq": seq})

        # Then
        history = _load_history(meta_json_path, CSV_TYPE)
        saved_seqs = [entry["seq"] for entry in history]
        expected_seqs = list(range(total_count - 1, total_count - 1 - MAX_HISTORY_COUNT, -1))
        assert saved_seqs == expected_seqs

    def test_does_not_mutate_input_metadata(self, meta_json_path: Path):
        """
        목적: 호출자가 넘긴 dict를 변경하지 않는다 (데이터 불변성).

        Given: timestamp 키가 없는 메타데이터 dict
        When: save_metadata 호출
        Then: 원본 dict에는 timestamp가 추가되지 않는다
        """
        # Given
        metadata: dict[str, Any] = {"rows": 10}

        # When
        save_metadata(CSV_TYPE, metadata)

        # Then
        assert metadata == {"rows": 10}
        assert meta_json_path.exists()

    def test_keeps_other_types_independent(self, meta_json_path: Path):
        """
        목적: 타입별 이력이 서로 덮어쓰이지 않는다.

        Given: 서로 다른 두 타입의 저장 이력
        When: 두 타입을 번갈아 저장
        Then: 각 타입의 이력이 독립적으로 보존된다
        """
        # Given / When
        save_metadata(CSV_TYPE, {"seq": 0})
        save_metadata(OTHER_CSV_TYPE, {"seq": 100})
        save_metadata(CSV_TYPE, {"seq": 1})

        # Then
        assert [entry["seq"] for entry in _load_history(meta_json_path, CSV_TYPE)] == [1, 0]
        assert [entry["seq"] for entry in _load_history(meta_json_path, OTHER_CSV_TYPE)] == [100]

    def test_accepts_empty_metadata(self, meta_json_path: Path):
        """
        목적: 빈 메타데이터도 타임스탬프만 담긴 이력으로 저장된다 (경계 조건).

        Given: 빈 dict
        When: save_metadata 호출
        Then: timestamp 키만 가진 이력 1건이 저장된다
        """
        # Given / When
        save_metadata(CSV_TYPE, {})

        # Then
        history = _load_history(meta_json_path, CSV_TYPE)
        assert list(history[0].keys()) == ["timestamp"]
