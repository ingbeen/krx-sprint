#!/usr/bin/env python3
"""
계획서 선행 게이트 훅 (PreToolUse)

문서(`docs/**`, `**/*.md`)를 제외한 프로젝트 파일을 편집하려 할 때, 이번 세션에 계획서
활동이 없었다면 권한 프롬프트(`ask`)로 승격시킨다. auto 모드에서도 조용히 통과되지 않는다.

오타·주석·로그 메시지 수정은 규칙상 계획서 예외이므로 차단(`deny`)이 아니라 `ask` 를
사용한다. 사용자가 승인하면 `plan_lint.py` 가 세션 마커를 남겨 이후 편집은 통과시킨다.

경로 판정·마커 규약·규약 채택 판정은 `plan_lint.py` 가 소유하며 여기서 재사용한다.

입력: PreToolUse 훅 JSON (stdin)
출력: 게이트 대상이면 `permissionDecision: ask` (stdout), 아니면 무출력
"""

import json
import sys

from plan_lint import is_gated, marker_path, project_uses_plans, relative_path

GATE_REASON = (
    "계획서 없이 코드를 변경하려 합니다.\n"
    "프로젝트 규칙상 오타·주석·로그 메시지 수정을 제외한 모든 코드 변경은 "
    "`docs/plans/PLAN_<short_name>.md` 를 먼저 작성해야 합니다.\n"
    "계획서를 작성하려면 `/plan` 스킬을 사용하세요. "
    "예외에 해당하는 사소한 수정이면 이 요청을 승인하면 됩니다."
)


def main() -> int:
    """
    훅 진입점: stdin JSON을 읽어 계획서 선행 여부를 판정합니다.

    Returns:
        int: 종료 코드 (항상 0. 판정은 stdout JSON으로 전달)
    """
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    cwd = str(payload.get("cwd") or "")
    if not project_uses_plans(cwd):
        return 0

    session_id = str(payload.get("session_id") or "")
    if not session_id:
        # 세션을 식별할 수 없으면 마커 추적이 불가능하다. 반복 프롬프트를 피해 통과시킨다
        return 0

    tool_input = payload.get("tool_input") or {}
    rel_path = relative_path(str(tool_input.get("file_path") or ""), cwd)

    if rel_path is None or not is_gated(rel_path):
        return 0

    if marker_path(session_id).exists():
        return 0

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": GATE_REASON,
            }
        },
        sys.stdout,
        ensure_ascii=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
