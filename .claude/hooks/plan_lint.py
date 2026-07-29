#!/usr/bin/env python3
"""
계획서 불변조건 검사 훅 (PostToolUse)

계획서(`docs/plans/PLAN_*.md`)가 저장될 때 Done 처리 규칙을 기계적으로 검사하고,
위반 시 `decision: block`으로 되돌린다. 프롬프트 수준의 경고를 대체하는 결정적 게이트다.

동시에 `plan_gate.py`가 참조하는 세션 마커를 기록한다. 마커는 "이번 세션에서
계획서 활동이 있었거나, 게이트 대상 파일 편집이 이미 승인됐다"는 사실을 나타낸다.

입력: PostToolUse 훅 JSON (stdin)
출력: 위반 시 `{"decision": "block", "reason": ...}` (stdout), 정상 시 무출력
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

# 계획서 경로 및 게이트 대상 경로 (프로젝트 루트 기준 상대경로)
PLAN_PREFIX = "docs/plans/PLAN_"
GATED_DIRS = ("src/", "scripts/", "tests/")
GATED_SUFFIX = ".py"

# 계획서 본문 판정용 패턴
STATUS_PREFIX = "**상태**:"
STATUS_DONE = "**상태**: ✅ Done"
FIXED_RULES_HEADER = "## 0) 고정 규칙"
UNCHECKED_BOX = re.compile(r"^\s*- \[ \]", re.MULTILINE)
VALIDATION_LINE = re.compile(r"^\s*- \[[ xX]\].*validate_project\.py.*$", re.MULTILINE)
# 실제 계획서는 `failed=**0**` 처럼 마크다운 강조를 섞어 쓰므로 강조 기호를 허용한다
FAILED_COUNT = re.compile(r"failed=[*_]*(\d+)")
SKIPPED_COUNT = re.compile(r"skipped=[*_]*(\d+)")


def is_done(text: str) -> bool:
    """
    계획서 상태가 Done인지 판정합니다.

    템플릿의 `**상태**: 🟡 Draft / 🔄 In Progress / ✅ Done` 처럼 선택지가 나열된
    줄을 Done으로 오판하지 않도록, 첫 상태 줄이 정확히 Done 표기일 때만 True 입니다.

    Args:
        text: 계획서 전문

    Returns:
        bool: Done 상태 여부
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(STATUS_PREFIX):
            return stripped == STATUS_DONE
    return False


def check_plan(text: str) -> list[str]:
    """
    계획서 본문의 불변조건을 검사합니다.

    검사 항목:
        1. `## 0) 고정 규칙` 섹션 보존 여부 (상태와 무관하게 항상 검사)
        2. Done 상태에서 미완료 체크박스(`- [ ]`) 잔존 여부
        3. Done 상태에서 Validation 결과의 failed/skipped 가 0인지 여부

    Args:
        text: 계획서 전문

    Returns:
        list[str]: 위반 메시지 목록. 비어 있으면 통과
    """
    violations: list[str] = []

    # 1. 고정 규칙 섹션은 삭제/변형이 금지된다
    if FIXED_RULES_HEADER not in text:
        violations.append(f"`{FIXED_RULES_HEADER}` 섹션이 없습니다. 이 섹션은 삭제/수정이 금지됩니다.")

    if not is_done(text):
        return violations

    # 2. Done 이면 미완료 체크박스가 남아 있을 수 없다
    unchecked = len(UNCHECKED_BOX.findall(text))
    if unchecked > 0:
        violations.append(f"상태가 Done 인데 미완료 체크박스가 {unchecked}개 남아 있습니다. 모두 [x] 로 만들거나 상태를 In Progress 로 되돌리세요.")

    # 3. Done 이면 Validation 결과가 failed=0, skipped=0 이어야 한다
    for line in VALIDATION_LINE.findall(text):
        failed = FAILED_COUNT.search(line)
        skipped = SKIPPED_COUNT.search(line)
        if failed is None or skipped is None:
            violations.append(f"상태가 Done 인데 Validation 줄에 passed/failed/skipped 수가 기록되지 않았습니다: {line.strip()}")
            continue
        if int(failed.group(1)) != 0 or int(skipped.group(1)) != 0:
            violations.append(f"상태가 Done 인데 Validation 결과가 failed=0, skipped=0 이 아닙니다: {line.strip()}")

    return violations


def marker_path(session_id: str) -> Path:
    """
    세션별 게이트 통과 마커 경로를 반환합니다.

    Args:
        session_id: 훅 입력으로 전달된 세션 ID

    Returns:
        Path: 마커 파일 경로
    """
    return Path(tempfile.gettempdir()) / "krx-sprint-plan-gate" / session_id


def relative_path(file_path: str, cwd: str) -> str | None:
    """
    편집 대상 파일을 프로젝트 루트 기준 상대경로(POSIX 구분자)로 변환합니다.

    Args:
        file_path: 훅 입력의 절대 경로
        cwd: 훅 입력의 작업 디렉토리

    Returns:
        str | None: 상대경로. 변환 불가 시 None
    """
    if not file_path or not cwd:
        return None
    try:
        return Path(os.path.relpath(file_path, cwd)).as_posix()
    except ValueError:
        # 다른 드라이브 등 상대경로 계산이 불가능한 경우
        return None


def is_gated(rel_path: str) -> bool:
    """
    계획서 게이트의 감시 대상 파일인지 판정합니다.

    Args:
        rel_path: 프로젝트 루트 기준 상대경로

    Returns:
        bool: 감시 대상 여부
    """
    return rel_path.endswith(GATED_SUFFIX) and rel_path.startswith(GATED_DIRS)


def touch_marker(session_id: str) -> None:
    """
    게이트 통과 마커를 기록합니다. 실패해도 훅 전체를 중단시키지 않습니다.

    Args:
        session_id: 훅 입력으로 전달된 세션 ID
    """
    if not session_id:
        return
    try:
        path = marker_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except OSError:
        pass


def main() -> int:
    """
    훅 진입점: stdin JSON을 읽어 계획서를 검사하고 마커를 기록합니다.

    Returns:
        int: 종료 코드 (항상 0. 차단은 stdout JSON으로 전달)
    """
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    session_id = str(payload.get("session_id") or "")
    tool_input = payload.get("tool_input") or {}
    rel_path = relative_path(str(tool_input.get("file_path") or ""), str(payload.get("cwd") or ""))

    if rel_path is None:
        return 0

    # 게이트 대상 파일 편집이 성사됐다는 것은 게이트를 통과했다는 뜻이다
    if is_gated(rel_path):
        touch_marker(session_id)
        return 0

    if not (rel_path.startswith(PLAN_PREFIX) and rel_path.endswith(".md")):
        return 0

    # 계획서 활동이 있었으므로 이후 코드 편집은 게이트를 통과시킨다
    touch_marker(session_id)

    try:
        text = Path(str(tool_input.get("file_path"))).read_text(encoding="utf-8")
    except OSError:
        return 0

    violations = check_plan(text)
    if not violations:
        return 0

    reason = "계획서 불변조건 위반 — 아래 항목을 수정한 뒤 다시 저장하세요.\n" + "\n".join(f"- {item}" for item in violations)
    json.dump({"decision": "block", "reason": reason}, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
