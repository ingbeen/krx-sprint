"""formatting 스모크 테스트

한글/영문 혼용 시의 터미널 폭 계산과 TableLogger 출력 계약을 고정한다.
"""

import logging

import pytest

from krx_sprint.utils.formatting import Align, TableLogger, _format_cell, _get_display_width

# 테스트 전용 로거 이름 (전역 krx_sprint 로거와 분리)
TEST_LOGGER_NAME = "tests.formatting"


class TestGetDisplayWidth:
    """문자폭 계산 계약을 고정한다."""

    def test_hangul_counts_two_columns(self):
        """
        목적: 전각 문자(한글)는 2칸으로 계산한다.

        Given: 한글 3글자
        When: _get_display_width 호출
        Then: 6칸으로 계산된다
        """
        assert _get_display_width("삼성전") == 6

    def test_ascii_counts_one_column(self):
        """
        목적: 반각 문자(영문·숫자)는 1칸으로 계산한다.

        Given: 영숫자 6글자
        When: _get_display_width 호출
        Then: 6칸으로 계산된다
        """
        assert _get_display_width("005930") == 6

    def test_mixed_text_sums_both_widths(self):
        """
        목적: 한글과 영문이 섞인 문자열도 합산 폭이 정확하다.

        Given: 한글 2글자 + 영문 3글자
        When: _get_display_width 호출
        Then: 4 + 3 = 7칸으로 계산된다
        """
        assert _get_display_width("종가KRW") == 7

    def test_empty_string_has_zero_width(self):
        """
        목적: 빈 문자열의 폭은 0이다 (경계 조건).

        Given: 빈 문자열
        When: _get_display_width 호출
        Then: 0칸이다
        """
        assert _get_display_width("") == 0


class TestFormatCell:
    """셀 정렬 계약을 고정한다."""

    def test_left_align_pads_right(self):
        """
        목적: 왼쪽 정렬은 오른쪽에 공백을 채운다 (한글 폭 반영).

        Given: 폭 4칸인 한글 2글자, 목표 폭 8칸
        When: LEFT 정렬로 포맷
        Then: 오른쪽에 공백 4칸이 붙는다
        """
        assert _format_cell("종가", 8, Align.LEFT) == "종가    "

    def test_right_align_pads_left(self):
        """
        목적: 오른쪽 정렬은 왼쪽에 공백을 채운다.

        Given: 폭 5칸인 숫자 문자열, 목표 폭 8칸
        When: RIGHT 정렬로 포맷
        Then: 왼쪽에 공백 3칸이 붙는다
        """
        assert _format_cell("83000", 8, Align.RIGHT) == "   83000"

    def test_center_align_puts_extra_padding_on_right(self):
        """
        목적: 가운데 정렬에서 남는 공백 1칸은 오른쪽에 붙는다.

        Given: 폭 2칸인 한글 1글자, 목표 폭 5칸 (여백 3칸)
        When: CENTER 정렬로 포맷
        Then: 왼쪽 1칸, 오른쪽 2칸으로 분배된다
        """
        assert _format_cell("가", 5, Align.CENTER) == " 가  "

    def test_returns_original_when_width_insufficient(self):
        """
        목적: 목표 폭이 내용보다 좁으면 잘라내지 않고 원본을 반환한다 (경계 조건).

        Given: 폭 6칸인 문자열, 목표 폭 3칸
        When: LEFT 정렬로 포맷
        Then: 원본 문자열이 그대로 반환된다
        """
        assert _format_cell("삼성전", 3, Align.LEFT) == "삼성전"


class TestTableLogger:
    """TableLogger의 출력 구조와 입력 검증 계약을 고정한다."""

    def _build_table(self) -> TableLogger:
        """테스트용 2컬럼 테이블 로거를 생성한다 (전체 폭 = 들여쓰기 2 + 10 + 8 = 20)."""
        columns = [("종목명", 10, Align.LEFT), ("종가", 8, Align.RIGHT)]
        return TableLogger(columns, logging.getLogger(TEST_LOGGER_NAME))

    def test_print_row_rejects_length_mismatch(self):
        """
        목적: 데이터 길이가 컬럼 수와 다르면 즉시 예외를 발생시킨다.

        Given: 컬럼 2개짜리 테이블
        When: 값 1개짜리 행을 출력
        Then: ValueError가 발생한다
        """
        # Given
        table = self._build_table()

        # When / Then
        with pytest.raises(ValueError, match="일치하지 않습니다"):
            table.print_row(["삼성전자"])

    def test_print_table_emits_header_title_rows_and_footer(self, caplog: pytest.LogCaptureFixture):
        """
        목적: 테이블 출력은 구분선-제목-헤더-구분선-데이터-구분선 순서로 구성된다.

        Given: 컬럼 2개, 데이터 1행
        When: print_table 호출
        Then: 6줄이 출력되고 구분선 폭이 전체 폭과 일치한다
        """
        # Given
        table = self._build_table()

        # When
        with caplog.at_level(logging.DEBUG, logger=TEST_LOGGER_NAME):
            table.print_table([["삼성전자", 83000]], title="스냅샷 요약")

        # Then
        messages = caplog.messages
        assert len(messages) == 6
        assert messages[0] == "=" * 20
        assert messages[1] == "스냅샷 요약"
        assert messages[3] == "-" * 20
        assert messages[5] == "=" * 20

    def test_header_line_aligns_by_display_width(self, caplog: pytest.LogCaptureFixture):
        """
        목적: 헤더 셀은 문자 개수가 아니라 터미널 폭 기준으로 정렬된다.

        Given: 한글 컬럼명 2개 ("종목명" 6칸 / "종가" 4칸)
        When: print_header 호출
        Then: 각 셀이 지정 폭(10, 8)을 채우도록 공백이 붙는다
        """
        # Given
        table = self._build_table()

        # When
        with caplog.at_level(logging.DEBUG, logger=TEST_LOGGER_NAME):
            table.print_header()

        # Then
        assert caplog.messages[1] == "  " + "종목명    " + "    종가"

    def test_print_row_converts_non_string_values(self, caplog: pytest.LogCaptureFixture):
        """
        목적: 숫자 값도 문자열로 변환되어 출력된다.

        Given: 종가가 int인 데이터 행
        When: print_row 호출
        Then: 숫자가 문자열로 출력된다
        """
        # Given
        table = self._build_table()

        # When
        with caplog.at_level(logging.DEBUG, logger=TEST_LOGGER_NAME):
            table.print_row(["삼성전자", 83000])

        # Then
        assert "83000" in caplog.messages[0]
