"""
계획서 불변조건 검사 훅(`.claude/hooks/plan_lint.py`)의 판정 로직 테스트.

훅은 프로덕션 패키지가 아닌 하네스 설정이라 `src/`에 두지 않는다.
따라서 `importlib`로 파일 경로에서 직접 로드해 순수 함수만 검증한다.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

HOOK_PATH = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "plan_lint.py"

# 검사 대상이 되는 최소 계획서 골격 (고정 규칙 섹션 + 상태 줄)
FIXED_RULES_SECTION = "## 0) 고정 규칙 (이 plan은 반드시 아래 규칙을 따른다)"
VALIDATION_OK = "- [x] `poetry run python validate_project.py` (passed=362, failed=0, skipped=0)"


def _load_hook() -> ModuleType:
    """
    훅 모듈을 파일 경로에서 직접 로드합니다.

    Returns:
        ModuleType: 로드된 plan_lint 모듈

    Raises:
        RuntimeError: 모듈 스펙을 만들 수 없는 경우
    """
    spec = importlib.util.spec_from_file_location("plan_lint_under_test", HOOK_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"내부 불변조건 위반: 훅 모듈 스펙 생성 실패 (HOOK_PATH={HOOK_PATH})")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def hook() -> ModuleType:
    """plan_lint 모듈을 모듈 스코프로 한 번만 로드하는 픽스처."""
    return _load_hook()


def _build_plan(status: str, body: str = "", validation: str = VALIDATION_OK) -> str:
    """
    테스트용 계획서 본문을 조립합니다.

    Args:
        status: `**상태**:` 줄에 들어갈 전체 문자열
        body: 상태 줄과 Validation 줄 사이에 삽입할 본문
        validation: Validation 체크박스 줄

    Returns:
        str: 조립된 계획서 전문
    """
    return f"# Implementation Plan: 테스트\n\n{status}\n\n{FIXED_RULES_SECTION}\n\n{body}\n\n{validation}\n"


class TestFixedRulesSection:
    """고정 규칙 섹션 보존 검사 (상태와 무관하게 항상 적용)."""

    def test_고정_규칙_섹션이_없으면_위반(self, hook: ModuleType) -> None:
        """
        목적: 템플릿의 `## 0) 고정 규칙` 섹션 삭제를 차단하는 계약을 고정

        Given: 고정 규칙 섹션이 빠진 계획서
        When: check_plan 실행
        Then: 위반 목록에 해당 항목이 포함된다
        """
        # Given
        text = "# Implementation Plan: 테스트\n\n**상태**: 🔄 In Progress\n"

        # When
        violations = hook.check_plan(text)

        # Then
        assert any("고정 규칙" in item for item in violations)

    def test_고정_규칙_섹션이_있으면_통과(self, hook: ModuleType) -> None:
        """
        목적: 정상 계획서가 오탐으로 차단되지 않음을 고정

        Given: 고정 규칙 섹션이 있고 상태가 In Progress 인 계획서
        When: check_plan 실행
        Then: 위반이 없다
        """
        # Given
        text = _build_plan("**상태**: 🔄 In Progress", body="- [ ] 아직 남은 작업")

        # When
        violations = hook.check_plan(text)

        # Then
        assert violations == []


class TestDoneDetection:
    """Done 상태 판정 (템플릿의 선택지 나열 줄 오탐 방지)."""

    def test_템플릿의_상태_선택지_줄은_Done이_아니다(self, hook: ModuleType) -> None:
        """
        목적: `🟡 Draft / 🔄 In Progress / ✅ Done` 나열 줄을 Done으로 오판하지 않는 계약을 고정

        Given: 상태 줄에 세 선택지가 모두 나열된 계획서(새 계획서 초안 형태)
        When: is_done 실행
        Then: False 를 반환한다
        """
        # Given
        text = _build_plan("**상태**: 🟡 Draft / 🔄 In Progress / ✅ Done")

        # When
        result = hook.is_done(text)

        # Then
        assert result is False

    def test_상태_줄이_없으면_Done이_아니다(self, hook: ModuleType) -> None:
        """
        목적: 상태 줄 부재 시 Done 검사를 건너뛰는 계약을 고정

        Given: 상태 줄이 없는 문서
        When: is_done 실행
        Then: False 를 반환한다
        """
        # Given
        text = f"# 제목\n\n{FIXED_RULES_SECTION}\n"

        # When
        result = hook.is_done(text)

        # Then
        assert result is False

    def test_정확한_Done_표기만_Done으로_인정(self, hook: ModuleType) -> None:
        """
        목적: `**상태**: ✅ Done` 정확 일치만 Done으로 보는 계약을 고정

        Given: 상태가 정확히 Done 인 계획서
        When: is_done 실행
        Then: True 를 반환한다
        """
        # Given
        text = _build_plan("**상태**: ✅ Done")

        # When
        result = hook.is_done(text)

        # Then
        assert result is True


class TestUncheckedBoxes:
    """Done 상태에서 미완료 체크박스 잔존 검사."""

    def test_Done인데_미체크가_남으면_위반(self, hook: ModuleType) -> None:
        """
        목적: "Done 이면 미완료 항목이 없어야 한다"는 규칙을 기계적으로 고정

        Given: 상태가 Done 이면서 미완료 체크박스 2개가 남은 계획서
        When: check_plan 실행
        Then: 위반 목록에 미완료 개수가 보고된다
        """
        # Given
        text = _build_plan("**상태**: ✅ Done", body="- [ ] 목표 1\n- [x] 목표 2\n- [ ] 목표 3")

        # When
        violations = hook.check_plan(text)

        # Then
        assert any("미완료 체크박스가 2개" in item for item in violations)

    def test_Done이고_전부_체크면_통과(self, hook: ModuleType) -> None:
        """
        목적: 정상 완료 계획서가 차단되지 않음을 고정

        Given: 상태가 Done 이고 모든 체크박스가 [x] 인 계획서
        When: check_plan 실행
        Then: 위반이 없다
        """
        # Given
        text = _build_plan("**상태**: ✅ Done", body="- [x] 목표 1\n- [x] 목표 2")

        # When
        violations = hook.check_plan(text)

        # Then
        assert violations == []

    def test_In_Progress면_미체크를_허용(self, hook: ModuleType) -> None:
        """
        목적: 진행 중 계획서는 미완료 항목이 정상이라는 계약을 고정

        Given: 상태가 In Progress 이고 미완료 체크박스가 남은 계획서
        When: check_plan 실행
        Then: 위반이 없다
        """
        # Given
        text = _build_plan("**상태**: 🔄 In Progress", body="- [ ] 목표 1\n- [ ] 목표 2")

        # When
        violations = hook.check_plan(text)

        # Then
        assert violations == []


class TestValidationCounts:
    """Done 상태에서 Validation 결과 검사."""

    @pytest.mark.parametrize(
        "validation",
        [
            "- [x] `poetry run python validate_project.py` (passed=362, failed=1, skipped=0)",
            "- [x] `poetry run python validate_project.py` (passed=362, failed=0, skipped=3)",
        ],
    )
    def test_Done인데_failed나_skipped가_0이_아니면_위반(self, hook: ModuleType, validation: str) -> None:
        """
        목적: "Done 조건은 failed=0 그리고 skipped=0" 규칙을 고정

        Given: 상태가 Done 이고 Validation 결과에 0이 아닌 값이 있는 계획서
        When: check_plan 실행
        Then: 위반 목록에 해당 항목이 보고된다
        """
        # Given
        text = _build_plan("**상태**: ✅ Done", body="- [x] 목표 1", validation=validation)

        # When
        violations = hook.check_plan(text)

        # Then
        assert any("failed=0, skipped=0 이 아닙니다" in item for item in violations)

    def test_마크다운_강조가_섞인_숫자도_인식(self, hook: ModuleType) -> None:
        """
        목적: 실제 계획서의 `failed=**0**` 표기를 "숫자 미기록"으로 오판하지 않는 계약을 고정

        Given: Validation 숫자가 마크다운 강조로 감싸인 완료 계획서
        When: check_plan 실행
        Then: 위반이 없다
        """
        # Given
        validation = "- [x] `poetry run python validate_project.py` (passed=**199**, failed=**0**, skipped=**0**)"
        text = _build_plan("**상태**: ✅ Done", body="- [x] 목표 1", validation=validation)

        # When
        violations = hook.check_plan(text)

        # Then
        assert violations == []

    def test_Done인데_숫자가_미기록이면_위반(self, hook: ModuleType) -> None:
        """
        목적: 템플릿 자리표시자를 남긴 채 Done 처리하는 것을 차단하는 계약을 고정

        Given: Validation 줄에 숫자 대신 자리표시자가 남은 완료 계획서
        When: check_plan 실행
        Then: 위반 목록에 "기록되지 않았습니다"가 보고된다
        """
        # Given
        validation = r"- [x] `poetry run python validate_project.py` (passed=\_\_, failed=\_\_, skipped=\_\_)"
        text = _build_plan("**상태**: ✅ Done", body="- [x] 목표 1", validation=validation)

        # When
        violations = hook.check_plan(text)

        # Then
        assert any("기록되지 않았습니다" in item for item in violations)


class TestGatedPaths:
    """계획서 게이트의 감시 대상 경로 판정."""

    @pytest.mark.parametrize(
        "rel_path",
        [
            "src/krx_sprint/common_constants.py",
            "scripts/data/collect_snapshot.py",
            "tests/test_params.py",
        ],
    )
    def test_감시_대상_경로(self, hook: ModuleType, rel_path: str) -> None:
        """
        목적: src/·scripts/·tests/ 아래 .py 가 게이트 대상이라는 계약을 고정

        Given: 감시 대상 상대경로
        When: is_gated 실행
        Then: True 를 반환한다
        """
        assert hook.is_gated(rel_path) is True

    @pytest.mark.parametrize(
        "rel_path",
        [
            "docs/ROADMAP.md",
            "scripts/CLAUDE.md",
            "README.md",
            "validate_project.py",
            "storage/snapshot/20260101.parquet",
        ],
    )
    def test_비감시_경로(self, hook: ModuleType, rel_path: str) -> None:
        """
        목적: 비코드 파일과 감시 폴더 밖 파일이 게이트에 걸리지 않는 계약을 고정

        Given: 감시 대상이 아닌 상대경로
        When: is_gated 실행
        Then: False 를 반환한다
        """
        assert hook.is_gated(rel_path) is False
