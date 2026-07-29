# Implementation Plan: 계획서 체계를 스킬·훅 계층으로 이관

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

---

**작성일**: 2026-07-29 16:12
**마지막 업데이트**: 2026-07-29 16:38
**관련 범위**: 하네스 설정 (.claude/), docs
**관련 문서**: [CLAUDE.md](../../CLAUDE.md), [tests/CLAUDE.md](../../tests/CLAUDE.md)

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

- [x] 목표 1: 계획서 **절차** 규칙을 `.claude/skills/plan/`으로 이관해, `docs/` 아래 아무 파일이나 열 때 따라오던 상시 로드를 제거한다.
- [x] 목표 2: 계획서 **불변조건**(Done 조건, 섹션 0 보존)을 PostToolUse 훅으로 **결정적으로** 검사한다. 프롬프트 수준의 이모지 경고를 대체한다.
- [x] 목표 3: "코드 변경 전 계획서 작성" **트리거**를 PreToolUse 훅(`ask`)으로 강제한다. auto 모드에서 무시 불가능하게 만든다.
- [x] 목표 4: 절차도 불변조건도 아닌 잔여 사실(`docs/archive` 정책)을 `.claude/rules/`의 path-scoped rule로 옮기고 [docs/CLAUDE.md](../CLAUDE.md)를 삭제한다.

## 2) 비목표(Non-Goals)

- 계획서 **내용 규칙 자체의 변경**. 이번 작업은 규칙을 옮기는 것이지 바꾸는 것이 아니다. 문구는 이관 과정에서 최소한으로만 손댄다.
- `scripts/CLAUDE.md`, `tests/CLAUDE.md`의 이관. 같은 논리가 적용될 여지가 있으나 이번 범위 밖이다.
- 전역(`~/.claude/`) 확장. 규칙이 `validate_project.py`·`poetry`·`docs/plans/`에 결합돼 있어 프로젝트 범위로 한정한다.
- 이미 완료된 `PLAN_*.md` 본문 수정. 섹션 0의 "생성된 plan 수정 금지" 규칙을 따른다.
- `/commit` 스킬(전역 `~/.claude/commands/commit.md`)의 수정.

## 3) 배경/맥락(Context)

### 현재 문제점 / 동기

현재 계획서 체계는 전부 **프롬프트 문장(prose)**으로만 구현돼 있다. 프로젝트에 `.claude/` 디렉토리 자체가 없다.

1. **상시 로드 낭비**: [docs/CLAUDE.md](../CLAUDE.md) 6.6KB는 계획서를 쓸 때만 필요한 절차인데, `docs/` 아래 스펙 문서·ROADMAP·COMMANDS를 열기만 해도 함께 로드된다.
2. **불변조건이 강제되지 않음**: `_template.md`의 `🚫 삭제/수정 금지 🚫`와 "Done 조건"은 순수 텍스트 검사로 100% 판정 가능한데도 모델의 주의력에 맡겨져 있다. 실제로 `PLAN_adjusted_collector.md`는 `✅ Done`이면서 §1 목표 체크박스 3개가 미체크 상태다 — 규칙 위반이 이미 실재한다.
3. **트리거가 확률적**: "코드 변경 전 계획서 작성"이 [CLAUDE.md](../../CLAUDE.md)의 문장으로만 존재한다. auto 모드는 매 편집을 사람이 검토하지 않는 모드이므로, 가장 강제력이 필요한 지점에서 가장 약한 수단을 쓰고 있다.
4. **중복**: [docs/CLAUDE.md](../CLAUDE.md) §4 Commit Messages는 전역 `/commit` 스킬이 이미 더 정교하게 커버한다.

공식 문서가 이 분류를 그대로 권고한다 (2026-07-29 확인):

- Skills: "Create a skill when ... **a section of CLAUDE.md has grown into a procedure rather than a fact.**"
- Memory: "Keep it to facts Claude should hold in every session... If an entry is a multi-step procedure or only matters for one part of the codebase, **move it to a skill or a path-scoped rule instead.**"
- Memory: "To block an action regardless of what Claude decides, **use a PreToolUse hook instead.**"
- Hooks: "A hook's `ask` **also forces a permission prompt in auto mode**: the classifier can still deny the tool call, but it can't approve the call silently."

### 영향받는 규칙(반드시 읽고 전체 숙지)

> 아래 문서에 기재된 규칙을 **모두 숙지**하고 준수합니다.

- 루트 [CLAUDE.md](../../CLAUDE.md)
- [docs/CLAUDE.md](../CLAUDE.md) (이번 작업으로 삭제되는 대상이자 이관 원본)
- [tests/CLAUDE.md](../../tests/CLAUDE.md) (테스트 추가)

### 확정된 설계 결정

| 항목 | 결정 | 근거 |
| --- | --- | --- |
| `docs/CLAUDE.md` 처리 | **완전 삭제** (A안) | 설정을 `.claude/` 한 곳에 모으는 것이 이번 작업의 목적. 잔여 사실은 path-scoped rule로 이동 |
| plan-gate 감시 대상 | `src/`, `scripts/`, `tests/` 아래 **`*.py`만** | [CLAUDE.md](../../CLAUDE.md)의 "모든 코드 변경"을 좁히지 않는다. `.md` 등 비코드는 제외해 오탐을 줄인다 |
| plan-gate 강도 | `ask` (deny 아님) | 오타·주석·로그 수정 예외가 규칙에 명시돼 있어 사용자 판단 여지를 남겨야 한다. `deny`는 `bypassPermissions`도 뚫어 탈출구가 없다 |
| plan-lint 강도 | `block` | 순수 텍스트 불변조건이라 오탐이 구조적으로 불가능하고 사용자 판단 여지도 없다 |
| plan-lint 배치 | **`settings.json`** (스킬 frontmatter `hooks` 아님) | 스킬 frontmatter 훅은 "스킬 활성 중"에만 동작한다. 스킬 호출 없이 PLAN 파일을 고치면 검사가 누락된다 |
| 훅 스크립트 언어 | Python | 기존 전역 훅(`block-db-connect.py`) 선례. PyRight `include`가 `src/tests/scripts`뿐이라 `.claude/`는 타입 체크 대상 밖 |

## 4) 완료 조건(Definition of Done)

> Done은 "서술"이 아니라 "체크리스트 상태"로만 판단합니다. (정의/예외는 `/plan` 스킬)

- [x] 기능 요구사항 충족 (목표 1~4 전부)
- [x] 회귀/신규 테스트 추가 (`plan_lint` 판정 로직)
- [x] 두 훅의 실동작을 실제 JSON 입력으로 검증하고 결과를 진행 로그에 기록
- [x] `poetry run python validate_project.py` 통과 (failed=0, skipped=0; passed/failed/skipped 수 기록)
- [x] `poetry run black .` 실행 완료 (마지막 Phase에서 자동 포맷 적용)
- [x] 필요한 문서 업데이트(README.md / `docs/COMMANDS.md` / CLAUDE.md / plan 등 — 각각 변경 여부 명시)
- [x] plan 체크박스 최신화(Phase/DoD/Validation 모두 반영)

## 5) 변경 범위(Scope)

### 변경 대상 파일(예상)

신규:

- `.claude/settings.json` — PreToolUse(plan-gate) + PostToolUse(plan-lint) 등록
- `.claude/hooks/plan_gate.py` — 계획서 없는 코드 편집을 `ask`로 승격
- `.claude/hooks/plan_lint.py` — PLAN 파일 불변조건 검사 + 게이트 통과 마커 기록
- `.claude/skills/plan/SKILL.md` — 계획서 작성 절차 (docs/CLAUDE.md에서 이관)
- `.claude/skills/plan/template.md` — `docs/plans/_template.md`에서 이관
- `.claude/rules/docs.md` — `paths: docs/**` 로 한정된 `docs/archive` 정책
- `tests/test_plan_lint.py` — plan_lint 판정 로직 테스트

삭제:

- `docs/CLAUDE.md`
- `docs/plans/_template.md`

수정:

- `CLAUDE.md` — 폴더별 CLAUDE.md 참고 규칙, 계획서 섹션, 디렉토리 구조를 스킬 포인터로 갱신. 하네스가 자동 수행하는 "폴더 CLAUDE.md 먼저 읽기" CRITICAL 지시문 제거
- `docs/ROADMAP.md` — `docs/CLAUDE.md` 참조를 `/plan` 스킬로 교체
- `START_PROMPT.md` — 동일

변경 없음(명시):

- `README.md`: **변경 없음** — 계획서 체계를 언급하지 않는다
- `docs/COMMANDS.md`: **변경 없음** — 실행 명령어(`poetry run` 등)가 추가·변경되지 않는다. 훅은 하네스가 실행하고, 스킬은 `/plan`으로 호출한다
- `docs/plans/PLAN_*.md` (완료된 7건): **수정 금지**. `docs/CLAUDE.md` 링크가 깨지지만 섹션 0 규칙을 우선한다 (§7 리스크 참고)
- `.gitignore`: **변경 없음** — `.claude/`는 커밋 대상이다

### 데이터/결과 영향

- 없음. `storage/` 산출물, 수집·검증·백테스트 로직에 일절 접근하지 않는다.
- 유일한 런타임 영향은 **개발 세션 중의 툴 호출 흐름**이다: 계획서 없이 `*.py`를 편집하면 권한 프롬프트가 뜨고, 잘못된 PLAN 파일 저장은 차단된다.

## 6) 단계별 계획(Phases)

### Phase 1 — plan 스킬 신설 및 템플릿 이관 (그린 유지)

**작업 내용**:

- [x] `.claude/skills/plan/SKILL.md` 작성 — [docs/CLAUDE.md](../CLAUDE.md)의 §계획서 운영 규칙 §1~3, Phase 구성 원칙, plans 네이밍, KST 표기, validate/Black 타이밍을 이관
- [x] frontmatter에 `description` / `when_to_use` / `paths: docs/plans/**` 지정
- [x] `docs/plans/_template.md` → `.claude/skills/plan/template.md` 이관 (SoT 포인터를 스킬 기준으로 갱신)
- [x] `docs/plans/_template.md` 삭제

**Validation**:

- [x] `/plan` 이 스킬 목록에 나타나고 호출 시 template.md 경로가 안내되는지 확인

---

### Phase 2 — 훅 구현 및 등록 (그린 유지)

**작업 내용**:

- [x] `.claude/hooks/plan_lint.py` 구현 — PLAN 파일이면 불변조건 검사, 게이트 대상 파일이면 통과 마커 기록
  - 검사 1: `**상태**: ✅ Done` 인데 `- [ ]` 가 남아있으면 block
  - 검사 2: `**상태**: ✅ Done` 인데 Validation 줄의 `failed`/`skipped` 가 0이 아니면 block
  - 검사 3: `## 0) 고정 규칙` 섹션이 사라졌으면 block
- [x] `.claude/hooks/plan_gate.py` 구현 — `src/`·`scripts/`·`tests/` 아래 `*.py` 편집 시 세션 내 PLAN 활동이 없으면 `ask`, 있으면 `defer`
- [x] `.claude/settings.json` 작성 — 두 훅을 `if` 경로 패턴과 함께 등록

**Validation**:

- [x] 위반 PLAN 본문을 표준입력으로 넣어 `decision: block` 이 나오는지 확인
- [x] 정상 PLAN 본문으로 통과(exit 0)하는지 확인
- [x] 마커 없는 상태의 `src/*.py` 입력으로 `permissionDecision: ask` 가 나오는지 확인

---

### Phase 3 — 규칙 이관 및 참조 갱신 (그린 유지)

**작업 내용**:

- [x] `.claude/rules/docs.md` 작성 — `paths: ["docs/**"]`, `docs/archive` 정책만 포함
- [x] `docs/CLAUDE.md` 삭제
- [x] `CLAUDE.md` 갱신 — CRITICAL 지시문 제거, 계획서 섹션을 `/plan` 포인터로 축소, 디렉토리 구조에 `.claude/` 반영
- [x] `docs/ROADMAP.md` / `START_PROMPT.md` 의 `docs/CLAUDE.md` 참조 교체

**Validation**:

- [x] `grep -rn "docs/CLAUDE.md"` 결과가 완료된 `PLAN_*.md` 7건 + 스펙 부록 A.2 1건(의도적 보존, §8 참고) 외에는 남지 않음

---

### Phase 4 — 테스트 추가 (그린 유지)

**작업 내용**:

- [x] `tests/test_plan_lint.py` 작성 — 검사 1·2·3 각각의 위반/정상 케이스
- [x] `importlib` 동적 로딩으로 `.claude/hooks/plan_lint.py` 임포트 (src/ 오염 없이)

**Validation**:

- [x] `poetry run pytest tests/test_plan_lint.py -v` 통과

---

### 마지막 Phase — 문서 정리 및 최종 검증

**작업 내용**

- [x] 필요한 문서 업데이트 (README.md 변경 없음 / `docs/COMMANDS.md` 변경 없음 — 확인 완료 기록)
- [x] `poetry run black .` 실행(자동 포맷 적용)
- [x] 변경 기능 및 전체 플로우 최종 검증
- [x] DoD 체크리스트 최종 업데이트 및 체크 완료
- [x] 전체 Phase 체크리스트 최종 업데이트 및 상태 확정

**Validation**:

- [x] `poetry run python validate_project.py` (passed=401, failed=0, skipped=0)

#### Commit Messages (Final candidates) — 5개 중 1개 선택

1. 하네스 / 계획서 규칙을 스킬·훅 계층으로 이관 (docs/CLAUDE.md 제거)
2. 하네스 / 계획서 Done 조건 결정적 검사 훅 추가 + 절차 규칙 스킬화
3. 하네스 / plan 스킬·plan_gate·plan_lint 신설, 프롬프트 규칙을 강제 계층으로 이전
4. 하네스 / 코드 변경 전 계획서 게이트 도입 및 docs 규칙 path-scoped rule 전환
5. 문서 / 계획서 운영 규칙 재배치 — 절차는 스킬, 불변조건은 훅, 사실은 rule

## 7) 리스크(Risks)

| 리스크 | 영향 | 완화책 |
| --- | --- | --- |
| plan-gate 오탐으로 오타 수정마다 프롬프트 | 작업 흐름 저해 | `deny` 대신 `ask` 사용. 세션 내 최초 1회만 걸리도록 통과 마커 사용. 감시 대상을 `*.py`로 한정 |
| plan-lint 오탐으로 정상 PLAN 저장 차단 | 작업 중단 | 검사를 텍스트 불변조건 3종으로만 한정. 테스트로 고정 |
| 완료된 `PLAN_*.md` 7건의 `docs/CLAUDE.md` 링크 깨짐 | 과거 문서 열람 시 404 | 섹션 0의 "생성된 plan 수정 금지"를 우선한다. 이들은 과거 기록이며 현행 규칙의 SoT가 아니다 |
| 스킬이 필요할 때 자동 로드되지 않음 | 절차 규칙 누락 | `paths: docs/plans/**` + plan-gate의 `permissionDecisionReason`에서 `/plan` 을 명시적으로 안내 |
| `.claude/` 워크스페이스 신뢰 대화상자 | 첫 실행 시 승인 필요 | 사용자에게 안내한다. 훅은 신뢰 승인 후 동작한다 |
| 훅 등록 후 재시작 필요 | 당장 효과 없음 | `settings.json` 훅 변경은 세션 재시작이 필요할 수 있음을 안내 |

## 8) 메모(Notes)

- 이 plan 자체는 `.claude/` 설정과 문서만 건드리며 비즈니스 로직에 접근하지 않는다.
- 훅 스크립트는 `.claude/hooks/`에 위치해 PyRight `include`(`src`/`tests`/`scripts`) 밖이다. Ruff는 `check .` 로 전체를 훑으므로 린트는 통과해야 한다.
- `PLAN_adjusted_collector.md`의 `✅ Done` + 미체크 3건은 **수정하지 않는다**(섹션 0 규칙). plan-lint 도입의 근거 사례로만 인용한다.
- `docs/데이터수집_스펙_v2.md` 부록 A.2의 `docs/CLAUDE.md` 행은 **의도적으로 보존한다**. 이 표는 qbt 프로젝트에서 무엇을 가져올지 기록한 부트스트랩 이력이며, 현행 규칙의 포인터가 아니다. 고치면 과거 결정 기록을 사후에 다시 쓰는 셈이 된다.
- `.claude/hooks/__pycache__/`는 테스트의 동적 로딩으로 생성되며 `.gitignore`의 `__pycache__/` 규칙에 이미 포함된다(`git check-ignore`로 확인). `.gitignore` 변경 불필요.

### 진행 로그 (KST)

- 2026-07-29 16:12: 계획서 작성. 설계 결정 6건 확정(§3). 공식 문서로 스킬/훅/rules 3계층 분류 검증 완료.
- 2026-07-29 16:20: Phase 1·2 완료. **Phase 2에서 계획 대비 이탈 1건** — settings.json에 `if` 경로 패턴을 쓰려 했으나 중첩 경로 glob(`Edit(src/**/*.py)`)의 정확한 의미를 공식 문서에서 확인하지 못했다. 검증되지 않은 동작에 의존하는 대신 `matcher`(`Edit|Write`) + 훅 스크립트 내부 경로 필터로 처리했다. 판정 로직이 전부 Python 안에 있어 테스트로 고정 가능하다는 부수 이점이 있다.
- 2026-07-29 16:24: 훅 실동작 검증 중 **plan_lint 오탐 발견 및 수정**. 실제 계획서는 `failed=**0**` 처럼 마크다운 강조를 섞어 쓰는데 정규식 `failed=(\d+)` 이 매치하지 못해 정상 계획서를 "숫자 미기록"으로 차단했다. `failed=[*_]*(\d+)` 로 수정. 기존 계획서 8건 재스캔 결과 실제 위반 1건(`PLAN_adjusted_collector.md`)만 차단되고 7건 통과 — 오탐 0.
- 2026-07-29 16:30: Phase 3 완료. **이 plan 자체의 SoT 포인터를 `/plan` 스킬로 갱신했다.** 템플릿에서 복사한 헤더가 이번 작업으로 삭제된 `docs/CLAUDE.md`를 가리켜 링크가 끊겼기 때문이다. 완료된 plan 7건은 섹션 0 규칙대로 손대지 않았다.
- 2026-07-29 16:36: Phase 4 완료. `tests/test_plan_lint.py` 20건 통과. 오탐 재발 방지용으로 "마크다운 강조 숫자 인식", "템플릿 상태 선택지 줄을 Done으로 오판하지 않음" 두 계약을 테스트로 고정했다.
- 2026-07-29 16:38: 마지막 Phase. `black .` 1건 재포맷 후 훅 회귀 재검증(결과 동일). `validate_project.py` 통과(passed=401, failed=0, skipped=0). README.md·docs/COMMANDS.md 변경 없음 확인 완료. **Done**
