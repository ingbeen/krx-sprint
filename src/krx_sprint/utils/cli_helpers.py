"""CLI 스크립트 공통 헬퍼 함수

CLI 스크립트에서 공통으로 사용하는 예외 처리 데코레이터를 제공한다.

학습 포인트:
1. 데코레이터 패턴: 함수를 감싸서 추가 기능을 제공하는 고급 기법
2. 함수를 인자로 받고 함수를 반환하는 고차 함수 개념
3. @functools.wraps: 원본 함수의 메타데이터를 보존하는 헬퍼
"""

import inspect
import logging
from collections.abc import Callable
from functools import wraps
from typing import Any


def cli_exception_handler(func: Callable[[], int]) -> Callable[[], int]:
    """
    CLI main() 함수의 예외를 일관되게 처리하는 데코레이터.

    학습 포인트:
    1. 데코레이터 함수: @를 사용해 함수에 적용
    2. Callable[[], int]: 인자가 없고 int를 반환하는 함수 타입
    3. 중첩 함수(nested function): 함수 안에 함수 정의

    모든 예외를 캐치하여 스택 트레이스를 포함한 에러 로그를 남기고 실패 코드(1)를 반환한다.
    로거는 함수가 정의된 모듈의 최상위 'logger' 변수를 자동으로 감지한다.

    예외 메시지는 스택 트레이스에 포함되므로 별도로 출력하지 않아 중복을 방지한다.

    사용 예시:
        @cli_exception_handler  # 이 줄이 데코레이터 적용
        def main() -> int:
            # 비즈니스 로직
            return 0

    Args:
        func: CLI의 main() 함수 (반환값: 0=성공, 1=실패)

    Returns:
        래핑된 함수 (원본 함수를 감싼 wrapper 함수)
    """

    # @wraps(func): 원본 함수의 이름, 문서 등 메타데이터를 wrapper에 복사
    # 이것이 없으면 wrapper.__name__이 'wrapper'가 되어 디버깅이 어려워짐
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> int:
        """실제로 실행될 래퍼 함수

        원본 함수를 try-except로 감싸서 예외 처리 기능을 추가
        """
        # 1. 로거 자동 감지
        # inspect.getmodule(func): func가 정의된 모듈 객체를 가져옴
        module = inspect.getmodule(func)

        # getattr(객체, "속성명", 기본값): 모듈에서 'logger' 변수를 찾음
        # 없으면 None 반환
        logger = getattr(module, "logger", None)

        # logger가 None이면 (모듈에 logger 변수가 없으면)
        if logger is None:
            # 폴백: 기본 로거 사용
            # __name__: 현재 모듈의 이름
            logger = logging.getLogger(__name__)
            logger.warning("모듈에 logger가 정의되지 않았습니다. 기본 로거를 사용합니다.")

        try:
            # 2. 원본 함수 실행
            # *args, **kwargs를 그대로 전달하여 원본 함수 호출
            return func(*args, **kwargs)

        except Exception:
            # 3. 에러 로깅 (스택 트레이스에 예외 타입과 메시지 포함)
            # exc_info=True: 예외 정보와 스택 트레이스를 로그에 포함
            logger.error("예외 발생", exc_info=True)

            # 4. 실패 코드 반환
            # Unix 관례: 0=성공, 1=실패
            return 1

    # 데코레이터는 wrapper 함수를 반환
    # 실제로는 func 대신 wrapper가 실행됨
    return wrapper
