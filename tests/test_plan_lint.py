"""
계획서 게이트 훅(`.claude/hooks/plan_gate.py`, `plan_lint.py`)의 판정 로직 테스트.

훅은 프로덕션 패키지가 아닌 하네스 설정이라 `src/`에 두지 않는다.
따라서 순수 함수는 `importlib`로 파일 경로에서 직접 로드해 검증하고,
출력·부작용(마커 파일)에 관한 계약은 훅을 subprocess로 실행해 검증한다.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[1] / ".claude" / "hooks"
HOOK_PATH = HOOKS_DIR / "plan_lint.py"
GATE_PATH = HOOKS_DIR / "plan_gate.py"

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
    """
    계획서 게이트의 감시 대상 경로 판정.

    제외 목록 방식이다. 화이트리스트(확장자 나열)는 새 언어·빌드 파일이 생길 때
    조용히 통과시켜 게이트 목적과 실패 방향이 반대이므로 쓰지 않는다.
    """

    @pytest.mark.parametrize(
        "rel_path",
        [
            "src/krx_sprint/common_constants.py",
            "scripts/data/collect_snapshot.py",
            "tests/test_params.py",
            "validate_project.py",
        ],
    )
    def test_파이썬_소스는_위치와_무관하게_대상(self, hook: ModuleType, rel_path: str) -> None:
        """
        목적: 특정 폴더 화이트리스트를 쓰지 않는다는 계약을 고정
              (루트의 validate_project.py 도 대상이어야 한다)

        Given: 프로젝트 안의 파이썬 파일 경로
        When: is_gated 실행
        Then: True 를 반환한다
        """
        assert hook.is_gated(rel_path) is True

    @pytest.mark.parametrize(
        "rel_path",
        [
            "src/main/java/com/example/App.java",
            "app/src/index.ts",
            "internal/server/handler.go",
        ],
    )
    def test_다른_언어_소스도_대상(self, hook: ModuleType, rel_path: str) -> None:
        """
        목적: 게이트가 언어·레이아웃에 종속되지 않는다는 계약을 고정
              (전역 이동 시 Java/TypeScript 프로젝트에서도 동작해야 한다)

        Given: 파이썬이 아닌 언어의 소스 경로
        When: is_gated 실행
        Then: True 를 반환한다
        """
        assert hook.is_gated(rel_path) is True

    @pytest.mark.parametrize(
        "rel_path",
        [
            "pyproject.toml",
            "pom.xml",
            "package.json",
            "pyrightconfig.json",
            ".claude/settings.json",
        ],
    )
    def test_빌드_설정_파일도_대상(self, hook: ModuleType, rel_path: str) -> None:
        """
        목적: 의존성·타입체커·하네스 설정 변경도 게이트 대상이라는 계약을 고정
              (영향도가 낮지 않은데 확장자 화이트리스트에서는 누락됐다)

        Given: 빌드/설정 파일 경로
        When: is_gated 실행
        Then: True 를 반환한다
        """
        assert hook.is_gated(rel_path) is True

    @pytest.mark.parametrize(
        "rel_path",
        [
            "docs/ROADMAP.md",
            "docs/plans/PLAN_foo.md",
            "docs/데이터수집_스펙_v2.md",
            "README.md",
            "scripts/CLAUDE.md",
        ],
    )
    def test_문서는_제외(self, hook: ModuleType, rel_path: str) -> None:
        """
        목적: 문서를 제외하는 계약을 고정
              계획서가 docs/plans/ 에 있어 게이트하면 순환(계획서를 쓰려면 계획서가 필요)이 생기고,
              규칙상 "코드 변경"에도 해당하지 않는다.

        Given: 문서 경로
        When: is_gated 실행
        Then: False 를 반환한다
        """
        assert hook.is_gated(rel_path) is False

    def test_프로젝트_밖_경로는_제외(self, hook: ModuleType) -> None:
        """
        목적: 작업 디렉토리 밖의 파일은 이 프로젝트의 규칙 대상이 아니라는 계약을 고정

        Given: `../` 로 시작하는 상대경로
        When: is_gated 실행
        Then: False 를 반환한다
        """
        assert hook.is_gated("../other-project/main.py") is False


class TestProjectUsesPlans:
    """계획서 규약 채택 여부 판정 (전역 배치 시 다른 프로젝트를 방해하지 않기 위한 자기 게이팅)."""

    def test_docs_plans_가_있으면_활성(self, hook: ModuleType, tmp_path: Path) -> None:
        """
        목적: 규약을 채택한 프로젝트에서 훅이 동작한다는 계약을 고정

        Given: docs/plans/ 디렉토리가 있는 작업 디렉토리
        When: project_uses_plans 실행
        Then: True 를 반환한다
        """
        # Given
        (tmp_path / "docs" / "plans").mkdir(parents=True)

        # When / Then
        assert hook.project_uses_plans(str(tmp_path)) is True

    @pytest.mark.parametrize("subdir", ["", "docs"])
    def test_docs_plans_가_없으면_비활성(self, hook: ModuleType, tmp_path: Path, subdir: str) -> None:
        """
        목적: 규약 미채택 프로젝트(예: 일반 Java 저장소)에서 무동작이라는 계약을 고정

        Given: docs/plans/ 가 없는 작업 디렉토리
        When: project_uses_plans 실행
        Then: False 를 반환한다
        """
        # Given
        if subdir:
            (tmp_path / subdir).mkdir()

        # When / Then
        assert hook.project_uses_plans(str(tmp_path)) is False

    def test_cwd가_비면_비활성(self, hook: ModuleType) -> None:
        """
        목적: 작업 디렉토리를 알 수 없을 때 안전하게 통과시키는 계약을 고정

        Given: cwd 가 빈 문자열
        When: project_uses_plans 실행
        Then: False 를 반환한다
        """
        assert hook.project_uses_plans("") is False


class TestPlansReferenced:
    """Bash 명령문에서 검사 대상 계획서를 추출하는 계약."""

    @pytest.fixture
    def plan_dir(self, tmp_path: Path) -> Path:
        """계획서 2건이 들어 있는 임시 프로젝트 루트를 만듭니다."""
        directory = tmp_path / "docs" / "plans"
        directory.mkdir(parents=True)
        (directory / "PLAN_alpha.md").write_text("본문", encoding="utf-8")
        (directory / "PLAN_beta.md").write_text("본문", encoding="utf-8")
        return tmp_path

    @pytest.mark.parametrize(
        "command",
        [
            "sed -i '' 's/a/b/' docs/plans/PLAN_alpha.md",
            "cat > docs/plans/PLAN_alpha.md <<EOF\n내용\nEOF",
            'python3 - <<PY\nPath("docs/plans/PLAN_alpha.md").write_text("x")\nPY',
        ],
    )
    def test_명령문이_언급한_계획서를_찾는다(self, hook: ModuleType, plan_dir: Path, command: str) -> None:
        """
        목적: Bash 우회(sed/cat/heredoc)로 수정되는 계획서를 검사 대상으로 잡는 계약을 고정

        Given: 계획서 파일명을 포함한 Bash 명령문
        When: plans_referenced 실행
        Then: 해당 계획서 경로 1건을 반환한다
        """
        # When
        result = hook.plans_referenced(command, str(plan_dir))

        # Then
        assert [p.name for p in result] == ["PLAN_alpha.md"]

    @pytest.mark.parametrize(
        "command",
        [
            "ls docs/plans/",
            "git status",
            "poetry run pytest tests/",
            "",
        ],
    )
    def test_계획서를_언급하지_않는_명령은_빈_목록(self, hook: ModuleType, plan_dir: Path, command: str) -> None:
        """
        목적: 무관한 명령이 기존 위반 계획서 때문에 차단되지 않는 계약을 고정
              (계획서 폴더 전수 검사를 하지 않는다는 설계 결정의 회귀 방지)

        Given: 계획서 파일명이 등장하지 않는 Bash 명령문
        When: plans_referenced 실행
        Then: 빈 목록을 반환한다
        """
        assert hook.plans_referenced(command, str(plan_dir)) == []

    def test_존재하지_않는_계획서는_제외(self, hook: ModuleType, plan_dir: Path) -> None:
        """
        목적: 파일명만 등장하고 실체가 없는 경우를 걸러내는 계약을 고정

        Given: 실재하지 않는 계획서를 언급한 명령문
        When: plans_referenced 실행
        Then: 빈 목록을 반환한다
        """
        assert hook.plans_referenced("cat docs/plans/PLAN_nope.md", str(plan_dir)) == []

    def test_여러_계획서를_중복없이_수집(self, hook: ModuleType, plan_dir: Path) -> None:
        """
        목적: 한 명령이 여러 계획서를 건드릴 때 모두 검사하고 중복은 제거하는 계약을 고정

        Given: 두 계획서를 언급하고 그중 하나가 두 번 등장하는 명령문
        When: plans_referenced 실행
        Then: 중복 없이 2건을 반환한다
        """
        # Given
        command = (
            "cp docs/plans/PLAN_alpha.md /tmp/x && sed -i '' s/a/b/ docs/plans/PLAN_alpha.md docs/plans/PLAN_beta.md"
        )

        # When
        result = hook.plans_referenced(command, str(plan_dir))

        # Then
        assert [p.name for p in result] == ["PLAN_alpha.md", "PLAN_beta.md"]

    def test_cwd가_비면_빈_목록(self, hook: ModuleType) -> None:
        """
        목적: 작업 디렉토리를 알 수 없을 때 안전하게 통과시키는 계약을 고정

        Given: cwd 가 빈 문자열
        When: plans_referenced 실행
        Then: 빈 목록을 반환한다
        """
        assert hook.plans_referenced("sed -i '' s/a/b/ docs/plans/PLAN_alpha.md", "") == []


# --- 통합 테스트: 훅을 subprocess 로 실행해 출력·부작용 계약을 검증한다 ---

SESSION_ID = "pytest-session"

# lint 를 통과하는 최소 계획서 (진행 중 상태, 고정 규칙 섹션 보유)
VALID_PLAN = f"# Plan\n\n**상태**: 🔄 In Progress\n\n{FIXED_RULES_SECTION}\n\n- [ ] 할 일\n"

# 고정 규칙 섹션이 없어 lint 에 걸리는 계획서
INVALID_PLAN = "# Plan\n\n**상태**: 🔄 In Progress\n\n- [ ] 할 일\n"


class HookRunner:
    """훅을 subprocess 로 실행하고 마커 파일을 관측하는 테스트 헬퍼."""

    def __init__(self, project: Path, tmpdir: Path) -> None:
        """
        Args:
            project: 임시 프로젝트 루트 (`docs/plans/` 포함)
            tmpdir: 훅의 `TMPDIR` 로 쓸 경로. 실제 마커 디렉토리를 오염시키지 않기 위함
        """
        self.project = project
        self.tmpdir = tmpdir

    @property
    def marker(self) -> Path:
        """훅이 기록할 세션 마커 경로."""
        return self.tmpdir / "krx-sprint-plan-gate" / SESSION_ID

    def run(self, hook_path: Path, tool_input: dict[str, str]) -> dict[str, object]:
        """
        훅을 실행하고 stdout JSON 을 파싱합니다.

        Args:
            hook_path: 실행할 훅 스크립트 경로
            tool_input: 훅 입력의 `tool_input` 값

        Returns:
            dict[str, object]: 파싱된 출력. 무출력이면 빈 dict

        Raises:
            RuntimeError: 훅이 0이 아닌 코드로 종료한 경우
        """
        payload = {"session_id": SESSION_ID, "cwd": str(self.project), "tool_input": tool_input}
        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env={"TMPDIR": str(self.tmpdir), "PATH": "/usr/bin:/bin"},
        )
        if result.returncode != 0:
            raise RuntimeError(f"내부 불변조건 위반: 훅이 비정상 종료했습니다 (stderr={result.stderr})")
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def write_plan(self, name: str, body: str) -> Path:
        """계획서 파일을 만들고 경로를 반환합니다."""
        path = self.project / "docs" / "plans" / name
        path.write_text(body, encoding="utf-8")
        return path


@pytest.fixture
def runner(tmp_path: Path) -> HookRunner:
    """`docs/plans/` 를 가진 임시 프로젝트와 격리된 TMPDIR 을 준비합니다."""
    project = tmp_path / "project"
    (project / "docs" / "plans").mkdir(parents=True)
    (project / "src").mkdir()
    marker_root = tmp_path / "tmpdir"
    marker_root.mkdir()
    return HookRunner(project, marker_root)


class TestGateOutput:
    """plan_gate 출력 계약."""

    def test_모델용_컨텍스트를_함께_출력한다(self, runner: HookRunner) -> None:
        """
        목적: 사용자용 사유(permissionDecisionReason)만 내보내면 모델에게는 "거부됨" 신호만
              전달돼 계획서 작성으로 넘어가지 않는다. 모델용 additionalContext 동반을 고정한다.

        Given: 계획서 활동이 없는 세션에서 게이트 대상 파일 편집
        When: plan_gate 실행
        Then: ask 결정과 함께 사용자용·모델용 문구가 모두 담긴다
        """
        # When
        out = runner.run(GATE_PATH, {"file_path": str(runner.project / "src" / "app.py")})

        # Then
        payload = out["hookSpecificOutput"]
        assert isinstance(payload, dict)
        assert payload["permissionDecision"] == "ask"
        assert payload["permissionDecisionReason"]
        assert "/plan" in str(payload["additionalContext"])

    def test_마커가_있으면_통과한다(self, runner: HookRunner) -> None:
        """
        목적: 게이트가 세션당 한 번만 걸린다는 계약을 고정

        Given: 세션 마커가 이미 존재
        When: plan_gate 실행
        Then: 아무것도 출력하지 않는다
        """
        # Given
        runner.marker.parent.mkdir(parents=True)
        runner.marker.touch()

        # When / Then
        assert runner.run(GATE_PATH, {"file_path": str(runner.project / "src" / "app.py")}) == {}

    def test_규약_미채택_프로젝트에서는_무동작(self, tmp_path: Path) -> None:
        """
        목적: docs/plans/ 가 없는 프로젝트(예: 일반 Java 저장소)에서 게이트가 걸리지 않는 계약을 고정
              전역 배치 시 다른 프로젝트를 방해하지 않기 위함이다.

        Given: docs/plans/ 가 없는 작업 디렉토리
        When: plan_gate 실행
        Then: 아무것도 출력하지 않는다
        """
        # Given
        bare = tmp_path / "java-repo"
        bare.mkdir()
        runner = HookRunner(bare, tmp_path / "t")
        runner.tmpdir.mkdir()

        # When / Then
        assert runner.run(GATE_PATH, {"file_path": str(bare / "src/main/java/App.java")}) == {}


class TestMarkerCondition:
    """plan_lint 의 마커 기록 조건 — 검사를 통과한 계획서만 게이트를 열 수 있다."""

    def test_정상_계획서는_마커를_남긴다(self, runner: HookRunner) -> None:
        """
        목적: 유효한 계획서 작성이 게이트를 통과시킨다는 계약을 고정

        Given: lint 를 통과하는 계획서
        When: plan_lint 실행
        Then: 차단 없이 마커가 생성된다
        """
        # Given
        path = runner.write_plan("PLAN_ok.md", VALID_PLAN)

        # When
        out = runner.run(HOOK_PATH, {"file_path": str(path)})

        # Then
        assert out == {}
        assert runner.marker.is_file()

    def test_위반_계획서는_마커를_남기지_않는다(self, runner: HookRunner) -> None:
        """
        목적: 차단된 계획서로 게이트가 열리면 "유효한 계획서 존재"라는 게이트 전제가 무너진다.
              차단 시 마커를 남기지 않는다는 계약을 고정한다.

        Given: 고정 규칙 섹션이 없어 lint 에 걸리는 계획서
        When: plan_lint 실행
        Then: block 결정이 나오고 마커는 생성되지 않는다
        """
        # Given
        path = runner.write_plan("PLAN_bad.md", INVALID_PLAN)

        # When
        out = runner.run(HOOK_PATH, {"file_path": str(path)})

        # Then
        assert out["decision"] == "block"
        assert not runner.marker.exists()

    def test_게이트_대상_파일_편집은_마커를_남긴다(self, runner: HookRunner) -> None:
        """
        목적: 편집이 성사됐다는 것은 게이트를 통과했다는 뜻이므로 마커를 남긴다는 계약을 고정

        Given: 게이트 대상 파일 경로
        When: plan_lint 실행 (PostToolUse)
        Then: 마커가 생성된다
        """
        # When
        out = runner.run(HOOK_PATH, {"file_path": str(runner.project / "src" / "app.py")})

        # Then
        assert out == {}
        assert runner.marker.is_file()

    def test_Bash_경로는_마커를_남기지_않는다(self, runner: HookRunner) -> None:
        """
        목적: Bash 는 읽기와 쓰기를 구분할 수 없으므로, 조회만으로 게이트가 열리지 않는 계약을 고정

        Given: 정상 계획서를 언급하는 Bash 명령
        When: plan_lint 실행
        Then: 차단도 없고 마커도 생기지 않는다
        """
        # Given
        runner.write_plan("PLAN_ok.md", VALID_PLAN)

        # When
        out = runner.run(HOOK_PATH, {"command": "head -5 docs/plans/PLAN_ok.md"})

        # Then
        assert out == {}
        assert not runner.marker.exists()
