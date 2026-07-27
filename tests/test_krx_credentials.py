"""krx_credentials 테스트

자격증명의 단일 출처가 `.env`임을 계약으로 고정한다.
"""

import os
from pathlib import Path

import pytest

from krx_sprint.collect.krx_credentials import ENV_KRX_ID, ENV_KRX_PW, load_krx_credentials


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch: pytest.MonkeyPatch):
    """테스트가 실제 환경 변수를 남기지 않도록 격리한다.

    monkeypatch가 테스트 전 값을 기억했다가 종료 시 되돌린다.
    """
    monkeypatch.setenv(ENV_KRX_ID, "테스트-초기값")
    monkeypatch.setenv(ENV_KRX_PW, "테스트-초기값")


def _write_env(path: Path, body: str) -> Path:
    """자격증명 파일을 만든다."""
    path.write_text(body, encoding="utf-8")
    return path


class TestLoadKrxCredentials:
    """`.env` 로딩 계약을 고정한다."""

    def test_loads_values_into_environment(self, tmp_path: Path):
        """
        목적: `.env`의 값이 환경 변수로 설정된다.

        Given: 자격증명이 담긴 .env
        When: load_krx_credentials 호출
        Then: 환경 변수에 값이 반영된다
        """
        # Given
        env_path = _write_env(tmp_path / ".env", "KRX_ID=계정\nKRX_PW=비밀번호\n")

        # When
        load_krx_credentials(env_path)

        # Then
        assert os.environ[ENV_KRX_ID] == "계정"
        assert os.environ[ENV_KRX_PW] == "비밀번호"

    def test_env_file_overrides_shell_variables(self, tmp_path: Path):
        """
        목적: 셸 환경 변수가 있어도 `.env` 값이 우선한다 (단일 출처 계약).

        Given: 셸 환경 변수가 이미 설정된 상태 + 다른 값의 .env
        When: load_krx_credentials 호출
        Then: .env 값으로 덮어써진다
        """
        # Given
        env_path = _write_env(tmp_path / ".env", "KRX_ID=env파일계정\nKRX_PW=env파일비밀번호\n")

        # When
        load_krx_credentials(env_path)

        # Then
        assert os.environ[ENV_KRX_ID] == "env파일계정"

    def test_rejects_missing_file(self, tmp_path: Path):
        """
        목적: `.env`가 없으면 셸 환경 변수로 대체하지 않고 중단한다.

        Given: 존재하지 않는 경로 (셸 환경 변수는 설정돼 있음)
        When: load_krx_credentials 호출
        Then: ValueError가 발생한다
        """
        # Given / When / Then
        with pytest.raises(ValueError, match="자격증명 파일이 없습니다"):
            load_krx_credentials(tmp_path / "없음.env")

    def test_rejects_empty_value(self, tmp_path: Path):
        """
        목적: 키는 있지만 값이 비어 있으면 거부한다 (경계 조건).

        Given: KRX_PW가 빈 .env
        When: load_krx_credentials 호출
        Then: ValueError에 빠진 키가 표시된다
        """
        # Given
        env_path = _write_env(tmp_path / ".env", "KRX_ID=계정\nKRX_PW=\n")

        # When / Then
        with pytest.raises(ValueError, match="KRX_PW"):
            load_krx_credentials(env_path)
