# Implementation Plan: 게이트 피드백 주입 및 마커 조건 강화

> 작성/운영 규칙(SoT): `/plan` 스킬([.claude/skills/plan/SKILL.md](../../.claude/skills/plan/SKILL.md))을 반드시 참고하세요.  
> (이 템플릿을 수정하거나 새로운 양식의 계획서를 만들 때도 해당 스킬을 포인터로 두고 준수합니다.)

**상태**: ✅ Done

---

🚫 **이 영역은 삭제/수정 금지** 🚫

**상태 옵션**: 🟡 Draft / 🔄 In Progress / ✅ Done

**Done 처리 규칙**:

- ✅ Done 조건: DoD 모두 [x] + `skipped=0` + `failed=0`
- ⚠️ **스킵이 1개라도 존재하면 Done 처리 금지 + DoD 테스트 항목 체크 금지**
- 상세: `/plan` 스킬의 "3) 스킵 및 완료 규칙" 참고
- 위 조건은 `.claude/hooks/plan_lint.py`가 저장 시 자동 검사합니다

---

**작성일**: 2026-07-30 09:33
**마지막 업데이트**: 2026-07-30 09:48
**관련 범위**: 하네스 설정 (.claude/hooks/)
**관련 문서**: [tests/CLAUDE.md](../../tests/CLAUDE.md), [PLAN_harness_portable_gate.md](PLAN_harness_portable_gate.md)

---

## 0) 고정 규칙 (이 plan은 반드시 아래 규칙을 따른다)

> 🚫 **이 영역은 삭제/수정 금지** 🚫
> 이 섹션(0)은 지워지면 안 될 뿐만 아니라 **문구가 수정되면 안 됩니다.**
> 규칙의 상세 정의/예외는 반드시 `/plan` 스킬을 따릅니다.

- `poetry run python validate_project.py`는 **마지막 Phase에서만 실행**한다. 실패하면 즉시 수정 후 재검증한다.
- Phase 0은 "레드(의도적 실패 테스트)" 허용, Phase 1부터는 **그린 유지**를 원칙으로 한다.
- 이미 생성된 plan은 **체크리스트 업데이트 외 수정 금지**한다.
- 스킵은 가능하면 **Phase 분해로 제거**한다.

---

## 1) 목표(Goal)

- [x] 목표 1: 게이트 발동 시 `additionalContext`로 **모델에게** 계획서 선행 지침을 주입해, 거부당한 뒤 편집을 재시도하는 대신 계획서 작성으로 넘어가게 한다.
- [x] 목표 2: 세션 마커를 **계획서가 검사를 통과한 경우에만** 기록해, 형식 위반 계획서로 게이트가 열리는 구멍을 막는다.

## 2) 비목표(Non-Goals)

- 계획서 품질 검사 항목 확대. 기존 9건 전수 조사 결과 필수 섹션·README/COMMANDS 명시·커밋 후보 위반이 **0건**이었다. 템플릿이 이미 방어하고 있어 검사를 늘리면 복잡도만 증가한다.
- 세션당 1회 제한의 변경. 좁히면 오타 수정마다 프롬프트가 떠 현 절충이 합리적이다.
- 파일명 규약(`PLAN_` 접두사) 검사. 스스로 규약을 깨는 경우라 방어 가치가 낮다.
- 전역(`~/.claude/`) 승격. 두 번째 프로젝트가 생길 때 진행한다.

## 3) 배경/맥락(Context)

### 현재 문제점 / 동기

**1. 게이트가 모델을 계획서 작성으로 유도하지 못한다 (실측된 결함)**

[PLAN_harness_portable_gate.md](PLAN_harness_portable_gate.md) 완료 후 실제 세션에서 게이트를 검증했다.
프롬프트는 정상 발동했고 거부도 됐지만, **거부당한 세션의 Claude는 "거부됐습니다"라고만 보고하고
계획서 작성으로 넘어가지 않았다.**

원인은 출력 필드다. 현재 훅은 `permissionDecisionReason`만 내보내는데 이는 **사용자에게 표시되는 문구**다.
모델에게는 "거부됨" 신호만 전달된다. PreToolUse 출력에는 모델용 필드가 따로 있다.

```json
{
  "hookSpecificOutput": {
    "permissionDecision": "ask",
    "permissionDecisionReason": "…",   // 사용자용
    "additionalContext": "…"           // 모델용 — 현재 미사용
  }
}
```

**2. 형식 위반 계획서로도 게이트가 열린다**

`plan_lint.py`의 Edit/Write 경로는 마커를 검사보다 먼저 기록한다.

```python
touch_marker(session_id)      # 마커 먼저
violations = lint_file(...)   # 검사는 나중
```

따라서 `docs/plans/PLAN_x.md`에 아무 내용이나 쓰면 lint에 차단되더라도 **마커는 이미 생겨
게이트가 열린다.** 게이트가 요구하는 것은 "계획서 파일을 건드렸다"가 아니라 "유효한 계획서를 썼다"여야 한다.

### 영향받는 규칙(반드시 읽고 전체 숙지)

> 아래 문서에 기재된 규칙을 **모두 숙지**하고 준수합니다.

- 루트 [CLAUDE.md](../../CLAUDE.md)
- [.claude/rules/python.md](../../.claude/rules/python.md)
- [tests/CLAUDE.md](../../tests/CLAUDE.md)
- 선행 계획서 [PLAN_harness_portable_gate.md](PLAN_harness_portable_gate.md), [PLAN_harness_hardening.md](PLAN_harness_hardening.md)

### 확정된 설계 결정

| 항목 | 결정 | 근거 |
| --- | --- | --- |
| 모델 지침 전달 | `hookSpecificOutput.additionalContext` | 공식 문서에 PreToolUse 출력 필드로 명시("Context for Claude"). `permissionDecisionReason`은 사용자용이라 목적이 다르다 |
| 지침 문구 | 승인·거부 양쪽에서 유효하게 서술 | 훅은 사용자의 결정을 알 수 없다. "거부되면 재시도하지 말고 계획서를 쓴다"까지 포함해야 한다 |
| 마커 기록 시점 | **lint 통과 후** | 게이트의 전제는 "유효한 계획서 존재"다. 차단된 계획서가 게이트를 열면 전제가 무너진다 |
| Bash 경로 마커 | 계속 기록하지 않음 | 읽기/쓰기를 구분할 수 없다는 기존 결정을 승계 |
| 테스트 방식 | **subprocess 통합 테스트** | 두 변경 모두 `main()`의 출력·부작용(마커 파일)에 관한 것이라 순수 함수 테스트로 잡히지 않는다 |
| 테스트 격리 | `TMPDIR` 환경변수로 마커 경로 분리 | `marker_path`가 `tempfile.gettempdir()`를 쓰므로 실제 마커를 오염시키지 않는다 |

## 4) 완료 조건(Definition of Done)

> Done은 "서술"이 아니라 "체크리스트 상태"로만 판단합니다. (정의/예외는 `/plan` 스킬)

- [x] 기능 요구사항 충족 (목표 1~2)
- [x] 회귀/신규 테스트 추가 (`additionalContext` 출력, 마커 기록 조건)
- [x] `poetry run python validate_project.py` 통과 (failed=0, skipped=0; passed/failed/skipped 수 기록)
- [x] `poetry run black .` 실행 완료 (마지막 Phase에서 자동 포맷 적용)
- [x] 필요한 문서 업데이트(README.md / `docs/COMMANDS.md` / CLAUDE.md / plan 등 — 각각 변경 여부 명시)
- [x] plan 체크박스 최신화(Phase/DoD/Validation 모두 반영)

## 5) 변경 범위(Scope)

### 변경 대상 파일(예상)

수정:

- `.claude/hooks/plan_gate.py` — `GATE_CONTEXT` 상수 추가 및 `additionalContext` 출력
- `.claude/hooks/plan_lint.py` — Edit/Write 경로에서 `touch_marker` 를 lint 통과 후로 이동
- `tests/test_plan_lint.py` — subprocess 기반 통합 테스트 클래스 추가

변경 없음(명시):

- `README.md`: **변경 없음** — 훅을 언급하지 않는다
- `docs/COMMANDS.md`: **변경 없음** — 실행 명령어/CLI 옵션이 바뀌지 않는다
- `CLAUDE.md`: **변경 없음** — 하네스 강제 설명이 이미 실제 동작과 일치한다(게이트 범위·차단 조건 불변)
- `.claude/settings.json`: **변경 없음** — matcher 불변
- `.claude/skills/plan/`: **변경 없음** — 계획서 절차 규칙 불변
- 완료된 `PLAN_*.md`: **수정 금지** (섹션 0)

### 데이터/결과 영향

- 없음. 비즈니스 로직과 `storage/` 산출물에 접근하지 않는다.
- 런타임 영향: 게이트 발동 시 모델 컨텍스트에 지침이 추가된다. 형식 위반 계획서를 쓴 경우 게이트가 계속 닫혀 있다.

## 6) 단계별 계획(Phases)

### Phase 1 — 두 훅 수정 (그린 유지)

**작업 내용**:

- [x] `plan_gate.py`: `GATE_CONTEXT` 추가 — 승인·거부 양쪽에서 유효한 문구로 작성하고 `additionalContext` 로 출력
- [x] `plan_lint.py`: Edit/Write 경로에서 위반이 있으면 `emit_block` 후 마커 없이 반환, 통과 시에만 `touch_marker`
- [x] 두 파일의 docstring을 변경된 동작에 맞게 갱신

**Validation**:

- [x] 게이트 발동 JSON 출력에 `additionalContext` 가 포함되는지 확인
- [x] 형식 위반 계획서 저장 시 마커가 생기지 않고, 정상 계획서 저장 시 생기는지 확인

---

### Phase 2 — 통합 테스트 추가 (그린 유지)

**작업 내용**:

- [x] `tests/test_plan_lint.py`: 훅을 subprocess 로 실행하는 헬퍼 추가 (`TMPDIR` 로 마커 격리, 임시 프로젝트 루트 구성)
- [x] `plan_gate` 출력 계약 테스트 — `permissionDecision: ask` + `additionalContext` 존재
- [x] `plan_lint` 마커 계약 테스트 — 정상 계획서는 마커 생성, 위반 계획서는 `decision: block` + 마커 미생성
- [x] 마커 존재 시 게이트가 통과하는지 테스트

**Validation**:

- [x] `poetry run pytest tests/test_plan_lint.py -v` 통과

---

### 마지막 Phase — 문서 정리 및 최종 검증

**작업 내용**

- [x] 필요한 문서 업데이트 (README.md / `docs/COMMANDS.md` 변경 여부 명시)
- [x] `poetry run black .` 실행(자동 포맷 적용)
- [x] 변경 기능 및 전체 플로우 최종 검증
- [x] DoD 체크리스트 최종 업데이트 및 체크 완료
- [x] 전체 Phase 체크리스트 최종 업데이트 및 상태 확정

**Validation**:

- [x] `poetry run python validate_project.py` (passed=432, failed=0, skipped=0)

#### Commit Messages (Final candidates) — 5개 중 1개 선택

1. 하네스 / 게이트 지침을 모델 컨텍스트로 주입하고 마커를 계획서 검사 통과 조건으로 변경
2. 하네스 / additionalContext 추가 — 게이트 거부 후 계획서 작성으로 이어지게 수정
3. 하네스 / 형식 위반 계획서가 게이트를 열던 구멍 차단 + 모델 피드백 경로 신설
4. 하네스 / plan_gate 피드백 강화, plan_lint 마커 기록을 lint 통과 후로 이동
5. 하네스 / 게이트 실효성 개선 및 훅 동작에 대한 subprocess 통합 테스트 추가

## 7) 리스크(Risks)

| 리스크 | 영향 | 완화책 |
| --- | --- | --- |
| `additionalContext` 문구가 승인 케이스에서 어색함 | 모델이 불필요하게 계획서를 요구 | "예외에 해당하면 그대로 진행" 조건을 문구에 포함 |
| 마커 조건 강화로 계획서 작성 중 게이트가 계속 닫힘 | 작업 흐름 저해 | 정상 템플릿은 In Progress 상태에서 lint 를 통과한다. 통과하지 못하는 계획서는 실제로 형식이 깨진 경우다 |
| subprocess 테스트가 실제 마커 디렉토리를 오염 | 개발 세션 게이트 오동작 | `TMPDIR` 을 `tmp_path` 로 지정해 격리하고 테스트에서 검증 |
| subprocess 테스트가 환경(python3 경로)에 의존 | 이식성 저하 | `sys.executable` 을 사용한다 |

## 8) 메모(Notes)

- 검사 항목 확대를 검토했으나 기존 9건 전수 조사에서 위반 0건이라 비목표로 확정했다. 근거: 필수 섹션 8/8(9건 전부), README/COMMANDS 명시 누락 0건, 커밋 후보 5개 이상 충족.
- 이번 변경으로 게이트의 전제가 "계획서 파일을 건드렸다" → "유효한 계획서를 썼다"로 강화된다.

### 진행 로그 (KST)

- 2026-07-30 09:33: 계획서 작성. 목표 1은 실제 세션에서 관측된 결함(거부 후 계획서 작성으로 넘어가지 않음), 목표 2는 코드 순서 검토에서 발견한 구멍이 근거.
- 2026-07-30 09:42: Phase 1 완료. `GATE_CONTEXT` 추가 및 `additionalContext` 출력, `touch_marker` 를 lint 통과 후로 이동. 실측 — 게이트 출력에 사용자용·모델용 문구가 모두 담기고, 형식 위반 계획서는 `block` + 마커 미생성, 정상 계획서는 통과 + 마커 생성, 마커 존재 시 게이트 통과.
- 2026-07-30 09:46: Phase 2 완료. `HookRunner` 헬퍼로 subprocess 통합 테스트 7건 추가(`TestGateOutput` 3건, `TestMarkerCondition` 4건). `TMPDIR` 을 `tmp_path` 로 지정해 실제 마커 디렉토리와 격리했고, `sys.executable` 을 써서 python3 경로 의존을 제거했다.
- 2026-07-30 09:48: 마지막 Phase. `black .` 재포맷 0건. `validate_project.py` 통과(passed=432, failed=0, skipped=0 — 테스트 7건 증가). README.md·docs/COMMANDS.md·CLAUDE.md 변경 없음 확인 완료. **Done**
