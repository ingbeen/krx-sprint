"""
krx-sprint Logging Module

로깅 정책:
- DEBUG: 개발 중 상세 정보 (필터링 가능)
- WARNING: 경고성 메시지
- ERROR: 에러 발생 시

로그 포맷: 시간 - 레벨 - 함수명 - 메시지

학습 포인트:
1. logging: Python 표준 라이브러리로 로그 기록 기능 제공
2. 클래스 상속: class ClickableFormatter(logging.Formatter) - 기존 클래스를 확장
3. VSCode에서 로그의 파일 경로를 클릭하면 해당 파일로 이동 가능하게 만듦
"""

import logging
import sys
from pathlib import Path
from typing import Any


class ClickableFormatter(logging.Formatter):
    """VSCode에서 클릭 가능한 파일 경로를 포함하는 로그 포맷터

    학습 포인트:
    1. 클래스 상속: logging.Formatter를 상속받아 커스텀 포맷터 생성
    2. super(): 부모 클래스의 메서드 호출
    3. *args, **kwargs: 가변 인자 - 임의 개수의 인자를 받을 수 있음
    """

    def __init__(self, *args: Any, **kwargs: Any):
        """초기화 메서드

        *args: 위치 인자들을 튜플로 받음 (예: __init__(1, 2, 3) → args=(1, 2, 3))
        **kwargs: 키워드 인자들을 딕셔너리로 받음 (예: __init__(a=1, b=2) → kwargs={'a': 1, 'b': 2})
        """
        # super().__init__(*args, **kwargs): 부모 클래스(logging.Formatter)의 __init__ 호출
        # 받은 인자를 그대로 부모 클래스에 전달
        super().__init__(*args, **kwargs)

        # 프로젝트 루트 경로를 찾아서 저장 (상대 경로 계산에 사용)
        self.project_root = self._find_project_root()

    def _find_project_root(self) -> Path:
        """
        pyproject.toml 또는 .git을 찾아 프로젝트 루트 반환

        학습 포인트:
        1. __file__: 현재 파일의 경로를 담은 특수 변수
        2. Path.resolve(): 심볼릭 링크를 해석하고 절대 경로로 변환
        3. for _ in range(10): 반복 횟수만 필요할 때 '_'를 변수명으로 사용

        Returns:
            프로젝트 루트 Path 객체
        """
        # __file__: 현재 실행 중인 파일의 경로
        # .resolve(): 절대 경로로 변환 (예: /home/user/project/src/qbt/utils/logger.py)
        # .parent: 부모 디렉토리 (예: /home/user/project/src/qbt/utils)
        current = Path(__file__).resolve().parent

        # 최대 10단계 상위 디렉토리까지 탐색
        # '_'는 사용하지 않는 변수를 나타내는 관례 (반복 횟수만 필요)
        for _ in range(10):
            # pyproject.toml 우선 확인 (Poetry 프로젝트의 설정 파일)
            # .exists(): 파일이나 디렉토리가 존재하는지 확인 (True/False 반환)
            if (current / "pyproject.toml").exists():
                return current

            # .git 대체 확인 (Git 저장소의 메타데이터 디렉토리)
            if (current / ".git").exists():
                return current

            # 한 단계 상위 디렉토리로 이동
            parent = current.parent

            # 루트 디렉토리 도달 확인
            # 루트에서는 parent == current (더 이상 올라갈 수 없음)
            if parent == current:  # 루트 디렉토리 도달
                break
            current = parent

        # 찾지 못하면 현재 작업 디렉토리 사용
        # Path.cwd(): Current Working Directory (명령을 실행한 위치)
        return Path.cwd()

    def format(self, record: logging.LogRecord) -> str:
        """
        로그 레코드에 상대 경로 정보 추가

        학습 포인트:
        1. try-except: 예외 처리 구문 - 오류가 발생할 수 있는 코드를 안전하게 실행
        2. f-string: f"{변수}" - 문자열 안에 변수를 쉽게 삽입하는 방법
        3. record.location: 동적으로 속성 추가 가능 (Python의 유연성)

        Args:
            record: 로그 레코드 (로그 발생 위치, 시간, 메시지 등 정보 포함)

        Returns:
            포맷팅된 로그 문자열
        """
        # record.pathname: 로그가 발생한 파일의 절대 경로
        pathname = Path(record.pathname)

        try:
            # 프로젝트 루트 기준 상대 경로 계산
            # relative_to(): 기준 경로로부터의 상대 경로 계산
            # 예: /home/user/project/src/main.py → src/main.py
            relative_path = pathname.relative_to(self.project_root)
        except ValueError:
            # ValueError: 상대 경로로 변환할 수 없을 때 발생 (외부 라이브러리 등)
            # 이 경우 절대 경로 그대로 사용
            relative_path = pathname

        # VSCode 클릭 가능 형식: 파일경로:줄번호
        # f-string: f"{변수}" 형식으로 문자열 안에 변수 값 삽입
        # record.lineno: 로그가 발생한 줄 번호
        record.location = f"{relative_path}:{record.lineno}"

        # 부모 클래스의 format 메서드 호출하여 최종 로그 문자열 생성
        return super().format(record)


def setup_logger(
    name: str = "krx_sprint",
    level: str | None = None,
) -> logging.Logger:
    """
    krx-sprint 프로젝트용 Logger 설정

    학습 포인트:
    1. 함수 기본 인자: name: str = "krx_sprint" - 인자를 안 넘기면 "krx_sprint" 사용
    2. str | None: 타입 힌트 - str 또는 None 타입 허용
    3. -> logging.Logger: 반환 타입 힌트 - 이 함수는 Logger 객체 반환

    Args:
        name: Logger 이름 (기본값: "krx_sprint")
        level: 로그 레벨 (DEBUG, WARNING, ERROR). None이면 DEBUG 사용

    Returns:
        설정된 Logger 인스턴스
    """
    # logging.getLogger(name): 이름으로 Logger 인스턴스 가져오기
    # 같은 이름으로 여러 번 호출하면 같은 인스턴스 반환 (싱글톤 패턴)
    logger = logging.getLogger(name)

    # 이미 핸들러가 설정되어 있으면 재설정하지 않음
    # logger.handlers: 리스트 - 비어있으면 False, 요소가 있으면 True
    if logger.handlers:
        return logger

    # 로그 레벨 설정
    # None 체크: level이 None이면 "DEBUG" 사용
    if level is None:
        level = "DEBUG"

    # getattr(객체, 속성명, 기본값): 객체에서 속성 값 가져오기
    # logging.DEBUG, logging.WARNING 등의 상수를 문자열로 접근
    # 예: getattr(logging, "DEBUG") → logging.DEBUG
    log_level = getattr(logging, level.upper(), logging.DEBUG)
    logger.setLevel(log_level)

    # 콘솔 핸들러 생성
    # StreamHandler: 스트림(여기서는 표준 출력)으로 로그 출력
    # sys.stdout: 표준 출력 스트림 (터미널에 출력)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)

    # 포맷 설정 (VSCode 클릭 가능한 형식)
    # fmt: 로그 포맷 문자열 (% 스타일 포맷팅)
    #   - %(asctime)s: 시간
    #   - %(levelname)s: 로그 레벨 (DEBUG, WARNING 등)
    #   - %(location)s: 파일 경로:줄번호 (우리가 추가한 커스텀 필드)
    #   - %(funcName)s: 함수명
    #   - %(message)s: 로그 메시지
    # datefmt: 날짜 포맷
    formatter = ClickableFormatter(
        fmt="%(asctime)s.%(msecs)03d %(levelname)s [%(location)s] [%(funcName)s] : %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(formatter)

    # 핸들러 추가
    # Logger는 여러 핸들러를 가질 수 있음 (파일, 콘솔, 네트워크 등)
    logger.addHandler(console_handler)

    # 상위 로거로 전파 방지
    # Python의 로거는 계층 구조인데, 전파를 막아 중복 출력 방지
    logger.propagate = False

    return logger


def get_logger(name: str = "krx_sprint") -> logging.Logger:
    """
    기존에 설정된 Logger를 가져오거나 새로 설정

    학습 포인트:
    1. not 연산자: 리스트가 비어있으면 True, 요소가 있으면 False
    2. 지연 초기화 패턴: 필요할 때만 설정

    Args:
        name: Logger 이름

    Returns:
        Logger 인스턴스
    """
    logger = logging.getLogger(name)

    # not logger.handlers: 핸들러 리스트가 비어있으면 True
    if not logger.handlers:
        # 핸들러가 없으면 기본 설정으로 초기화
        logger = setup_logger(name)
    return logger
