"""names 테스트

티커 → 종목명 매핑의 병합 계약과 티커 선행 0 보존을 고정한다 (스펙 §10.4).
"""

from pathlib import Path

import pytest

from krx_sprint.collect.names import load_names, merge_names


class TestMergeNames:
    """종목명 병합 계약을 고정한다."""

    def test_creates_file_and_roundtrips(self, tmp_path: Path):
        """
        목적: 최초 저장 후 그대로 다시 읽을 수 있다.

        Given: 파일이 없는 상태
        When: 매핑 2건 병합
        Then: 두 건이 그대로 조회된다
        """
        # Given
        path = tmp_path / "names.csv"

        # When
        merge_names({"005930": "삼성전자", "035720": "카카오"}, path=path)

        # Then
        assert load_names(path) == {"005930": "삼성전자", "035720": "카카오"}

    def test_preserves_ticker_leading_zero(self, tmp_path: Path):
        """
        목적: CSV 왕복에서 티커 선행 0이 사라지지 않는다 (int 변환 금지).

        Given: 선행 0으로 시작하는 티커
        When: 병합 후 다시 로드
        Then: 6자리 문자열이 유지된다
        """
        # Given
        path = tmp_path / "names.csv"

        # When
        merge_names({"005930": "삼성전자"}, path=path)

        # Then
        assert list(load_names(path)) == ["005930"]

    def test_updates_existing_ticker_name(self, tmp_path: Path):
        """
        목적: 사명이 바뀌면 새 이름으로 갱신한다.

        Given: 이미 저장된 티커
        When: 같은 티커를 다른 이름으로 병합
        Then: 새 이름으로 바뀐다
        """
        # Given
        path = tmp_path / "names.csv"
        merge_names({"042660": "대우조선해양"}, path=path)

        # When
        merge_names({"042660": "한화오션"}, path=path)

        # Then
        assert load_names(path) == {"042660": "한화오션"}

    def test_keeps_previously_saved_tickers(self, tmp_path: Path):
        """
        목적: 병합은 기존 매핑을 지우지 않는다 (폐지 종목 이름 보존).

        Given: 이미 저장된 티커
        When: 다른 티커만 담은 매핑을 병합
        Then: 기존 티커가 남아 있다
        """
        # Given
        path = tmp_path / "names.csv"
        merge_names({"117930": "한진해운"}, path=path)

        # When
        merge_names({"005930": "삼성전자"}, path=path)

        # Then
        assert load_names(path) == {"117930": "한진해운", "005930": "삼성전자"}

    def test_rejects_empty_mapping(self, tmp_path: Path):
        """
        목적: 빈 매핑으로 파일을 덮어쓰지 않는다 (경계 조건).

        Given: 빈 dict
        When: merge_names 호출
        Then: ValueError가 발생한다
        """
        # Given / When / Then
        with pytest.raises(ValueError, match="종목명"):
            merge_names({}, path=tmp_path / "names.csv")

    def test_load_returns_empty_for_missing_file(self, tmp_path: Path):
        """
        목적: 파일이 없으면 빈 매핑을 반환한다 (최초 실행, 경계 조건).

        Given: 존재하지 않는 파일
        When: load_names 호출
        Then: 빈 dict다
        """
        # Given / When / Then
        assert load_names(tmp_path / "없음.csv") == {}
