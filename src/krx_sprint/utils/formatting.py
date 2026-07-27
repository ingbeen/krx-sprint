"""터미널 출력 포맷팅 유틸리티

한글/영문 혼용 시 터미널 폭을 정확히 계산하여 정렬한다.

학습 포인트:
1. Enum (열거형): 관련된 상수들을 그룹화하는 방법
2. unicodedata.east_asian_width를 사용한 정확한 문자폭 계산
3. 클래스 기반 설계: TableLogger로 테이블 출력 로직을 캡슐화
"""

import logging
import unicodedata
from collections.abc import Sequence
from enum import Enum


class Align(Enum):
    """텍스트 정렬 방향을 나타내는 열거형

    학습 포인트:
    - Enum 상속: 관련된 상수를 그룹으로 관리
    - 사용 예: Align.LEFT, Align.RIGHT, Align.CENTER
    - 문자열보다 안전: 오타 방지, IDE 자동완성 지원
    """

    # 각 상수는 "이름 = 값" 형태로 정의
    LEFT = "left"  # 왼쪽 정렬
    RIGHT = "right"  # 오른쪽 정렬
    CENTER = "center"  # 가운데 정렬


def _get_display_width(text: str) -> int:
    """
    문자열의 실제 터미널 출력 폭을 계산한다.

    unicodedata.east_asian_width를 사용하여 문자폭을 정확히 계산한다.
    - W (Wide), F (Fullwidth): 2칸
    - 나머지 (Na, H, N, A): 1칸

    학습 포인트:
    1. unicodedata.east_asian_width: 유니코드 문자의 동아시아 폭 속성 반환
    2. W/F: 한글, 한자 등 전각 문자 (2칸)
    3. for 루프로 문자열의 각 문자 순회

    Args:
        text: 폭을 계산할 문자열

    Returns:
        터미널에서 차지하는 실제 폭 (칸 수)
    """
    width = 0

    for char in text:
        # unicodedata.east_asian_width(char): 문자의 동아시아 폭 속성 반환
        # 반환값: 'W' (Wide), 'F' (Fullwidth), 'Na' (Narrow), 'H' (Halfwidth), 'N' (Neutral), 'A' (Ambiguous)
        ea_width = unicodedata.east_asian_width(char)
        if ea_width in ("W", "F"):
            width += 2  # 전각 문자 (한글, 한자, 전각 기호 등)
        else:
            width += 1  # 반각 문자 (영문, 숫자, 반각 기호 등)

    return width


def _format_cell(text: str, width: int, align: Align = Align.LEFT) -> str:
    """
    터미널 폭을 고려하여 문자열을 정렬한다.

    한글과 영문이 섞인 문자열도 올바르게 정렬한다.

    Args:
        text: 정렬할 문자열
        width: 목표 폭 (칸 수)
        align: 정렬 방향 (Align enum)

    Returns:
        정렬된 문자열
    """
    content = str(text)
    content_width = _get_display_width(content)
    available_padding = width - content_width

    # 폭이 부족한 경우 원본 반환
    if available_padding <= 0:
        return content

    # 정렬 방향에 따라 패딩 적용
    if align == Align.LEFT:
        return content + " " * available_padding

    if align == Align.RIGHT:
        return " " * available_padding + content

    # CENTER: 좌우 균등 분배
    left_padding = available_padding // 2
    right_padding = available_padding - left_padding
    return " " * left_padding + content + " " * right_padding


def _format_row(cells: list[tuple[str, int, Align]], indent: int = 2) -> str:
    """
    여러 셀을 한 번에 포맷팅하여 행을 생성한다.

    Args:
        cells: [(텍스트, 폭, 정렬), ...] 튜플 리스트
        indent: 들여쓰기 칸 수

    Returns:
        포맷팅된 행 문자열
    """
    formatted_cells = [_format_cell(text, width, align) for text, width, align in cells]
    return " " * indent + "".join(formatted_cells)


class TableLogger:
    """테이블 형식으로 로그를 출력하는 클래스

    학습 포인트:
    1. 클래스: 관련된 데이터와 기능을 묶어 관리
    2. __init__: 생성자 메서드 - 인스턴스 초기화
    3. self: 인스턴스 자신을 가리키는 참조 (Java의 this와 유사)
    4. list[tuple[str, int, Align]]: 복잡한 타입 힌트 (튜플의 리스트)

    컬럼 정의를 받아서 테이블 구조를 설정하고, 헤더/데이터/푸터를 출력합니다.
    한글/영문 혼용 시 터미널 폭을 정확히 계산하여 정렬합니다.

    Examples:
        >>> # 컬럼 정의: (컬럼명, 폭, 정렬) 튜플의 리스트
        >>> columns = [
        ...     ("날짜", 12, Align.LEFT),
        ...     ("가격", 12, Align.RIGHT),
        ...     ("변동률", 10, Align.RIGHT),
        ... ]
        >>> # 테이블 로거 인스턴스 생성
        >>> table = TableLogger(columns, logger)
        >>> # 데이터 행 정의
        >>> rows = [
        ...     ["2024-01-01", "100.50", "+2.5%"],
        ...     ["2024-01-02", "102.00", "+1.5%"],
        ... ]
        >>> # 테이블 출력
        >>> table.print_table(rows, title="가격 변동 내역")
    """

    def __init__(self, columns: list[tuple[str, int, Align]], logger: logging.Logger, indent: int = 2) -> None:
        """
        TableLogger를 초기화한다.

        학습 포인트:
        1. __init__: 클래스 인스턴스 생성 시 자동 호출되는 생성자
        2. self.변수명: 인스턴스 변수 - 객체마다 독립적으로 존재
        3. 제너레이터 표현식: sum(width for _, width, _ in columns)
        4. 언패킹: (name, width, align) = ("날짜", 12, Align.LEFT)

        Args:
            columns: [(컬럼명, 폭, 정렬), ...] 튜플 리스트
            logger: 로거 인스턴스
            indent: 들여쓰기 칸 수 (기본값 2)
        """
        # self.변수: 이 인스턴스의 속성으로 저장
        # 다른 메서드에서 self.columns로 접근 가능
        self.columns = columns
        self.logger = logger
        self.indent = indent

        # 전체 테이블 폭 계산
        # sum(제너레이터): 제너레이터에서 생성된 값들의 합계
        # for _, width, _ in columns: 튜플에서 width만 추출 (_ 는 무시)
        self._total_width = indent + sum(width for _, width, _ in columns)

    def print_header(self, title: str | None = None) -> None:
        """
        테이블 헤더를 출력한다.

        상단 구분선, 제목(선택), 컬럼 헤더, 구분선을 출력합니다.

        Args:
            title: 테이블 제목 (None이면 제목 없음)
        """
        # 상단 구분선
        self.logger.debug("=" * self._total_width)

        # 제목 (있는 경우)
        if title:
            self.logger.debug(title)

        # 컬럼 헤더
        header_cells = [(name, width, align) for name, width, align in self.columns]
        header_line = _format_row(header_cells, self.indent)
        self.logger.debug(header_line)

        # 헤더 하단 구분선
        self.logger.debug("-" * self._total_width)

    def print_row(self, data: Sequence[str | int | float]) -> None:
        """
        데이터 행을 출력한다.

        Args:
            data: 컬럼 순서대로 정렬된 데이터 시퀀스 (str 외 타입은 내부에서 str 변환)

        Raises:
            ValueError: 데이터 길이가 컬럼 수와 일치하지 않는 경우
        """
        if len(data) != len(self.columns):
            raise ValueError(f"데이터 길이({len(data)})가 컬럼 수({len(self.columns)})와 일치하지 않습니다")

        cells = [(str(value), width, align) for value, (_, width, align) in zip(data, self.columns, strict=True)]
        row_line = _format_row(cells, self.indent)
        # print_header와 함수 이름 길이 차이를 보정하기 위해 공백 2칸 추가
        self.logger.debug("  " + row_line)

    def print_footer(self) -> None:
        """테이블 푸터(하단 구분선)를 출력한다."""
        self.logger.debug("=" * self._total_width)

    def print_table(self, rows: Sequence[Sequence[str | int | float]], title: str | None = None) -> None:
        """
        전체 테이블을 출력한다.

        헤더, 모든 데이터 행, 푸터를 한 번에 출력합니다.

        Args:
            rows: 데이터 행 시퀀스 (각 행은 컬럼 순서대로 정렬된 시퀀스)
            title: 테이블 제목 (None이면 제목 없음)
        """
        self.print_header(title)
        for row in rows:
            self.print_row(row)
        self.print_footer()
