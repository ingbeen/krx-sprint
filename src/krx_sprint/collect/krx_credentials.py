"""KRX 로그인 자격증명 로딩

자격증명의 단일 출처는 프로젝트 루트의 `.env`다. 셸 환경 변수가 이미 설정돼 있어도
`.env` 값으로 덮어써서, 어느 환경에서 실행하든 같은 계정을 쓰도록 고정한다.

pykrx는 import 시점에 환경 변수를 읽어 인증 세션을 만들므로,
이 함수는 **pykrx를 import 하기 전에** 호출해야 한다.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from krx_sprint.common_constants import ENV_FILE_PATH

# KRX 데이터포털 로그인 환경 변수
ENV_KRX_ID = "KRX_ID"
ENV_KRX_PW = "KRX_PW"

REQUIRED_KEYS = (ENV_KRX_ID, ENV_KRX_PW)


def load_krx_credentials(path: Path = ENV_FILE_PATH) -> None:
    """`.env`에서 KRX 자격증명을 읽어 환경 변수에 설정한다.

    Args:
        path: 자격증명 파일 경로

    Raises:
        ValueError: 파일이 없거나 필수 값이 비어 있는 경우
    """
    if not path.exists():
        raise ValueError(f"자격증명 파일이 없습니다: {path}. data.krx.co.kr 계정 정보를 담은 .env를 만드십시오 (docs/COMMANDS.md 참고)")

    # override=True: 셸 환경 변수보다 .env를 우선한다
    load_dotenv(path, override=True)

    missing = [key for key in REQUIRED_KEYS if not os.environ.get(key)]
    if missing:
        raise ValueError(f"{path}에 값이 비어 있습니다: {', '.join(missing)}")
