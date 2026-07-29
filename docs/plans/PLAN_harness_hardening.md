# Implementation Plan: 훅 우회 경로 차단 및 루트 CLAUDE.md 축소

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

**작성일**: 2026-07-29 17:09
**마지막 업데이트**: 2026-07-29 17:28
**관련 범위**: 하네스 설정 (.claude/), 루트 CLAUDE.md
**관련 문서**: [CLAUDE.md](../../CLAUDE.md), [tests/CLAUDE.md](../../tests/CLAUDE.md), [PLAN_harness_migration.md](PLAN_harness_migration.md)

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

- [x] 목표 1: Bash 툴을 통한 계획서 수정이 `plan_lint` 검사를 우회하는 구멍을 막는다.
- [x] 목표 2: 루트 [CLAUDE.md](../../CLAUDE.md)를 공식 권고치(200줄 미만)로 줄인다. 파이썬 코드 표준을 path-scoped rule로 이관하고, 전역 `~/.claude/CLAUDE.md`와 중복되는 섹션을 제거한다.

## 2) 비목표(Non-Goals)

- `plan_gate`(PreToolUse)의 Bash 대응. Bash 명령에서 "어떤 파일을 쓸 것인가"를 사전에 안정적으로 판정할 수 없다. 이번에는 **사후 검사(plan_lint)만** 보강한다.
- 적대적 우회 방어. 이 게이트는 실수·습관에 의한 우회를 막는 가드레일이지 보안 경계가 아니다.
- `scripts/CLAUDE.md`, `tests/CLAUDE.md` 이관. 하위 폴더 CLAUDE.md는 해당 폴더 파일을 다룰 때만 로드돼 이미 지연 로딩이므로 이득이 없다.
- 규칙 **내용**의 변경. 문구는 이관 과정에서 최소한으로만 손댄다.

## 3) 배경/맥락(Context)

### 현재 문제점 / 동기

**1. Bash 우회 (실제 발생)**

[PLAN_harness_migration.md](PLAN_harness_migration.md)에서 도입한 두 훅은 `matcher`가 `Edit|Write` 뿐이다.
`sed -i`, `cat >`, `python3 - <<PY` 로 파일을 쓰면 훅이 아예 호출되지 않는다.

이는 가설이 아니라 **실측된 사실**이다. 해당 plan의 마지막 단계에서 체크박스를 python heredoc으로
일괄 수정했고, 그때 `plan_lint`는 실행되지 않았다. auto 모드에서 Bash 파일 쓰기는 흔한 경로이므로
방치하면 게이트가 사실상 선택사항이 된다.

**2. 루트 CLAUDE.md 308줄**

공식 문서 권고: "target under 200 lines per CLAUDE.md file. **Longer files consume more context and
reduce adherence.**" 현재 308줄로 초과 상태이며, 두 종류의 낭비가 있다.

- 전역 `~/.claude/CLAUDE.md`(37줄)의 "사고 절차"·"수술적 변경"이 프로젝트 파일에 거의 그대로 중복된다.
- "구현 원칙"·"코딩 표준"·"로깅 정책"은 파이썬 코드를 다룰 때만 필요한데 `.md` 문서만 편집할 때도 항상 로드된다.

### 영향받는 규칙(반드시 읽고 전체 숙지)

> 아래 문서에 기재된 규칙을 **모두 숙지**하고 준수합니다.

- 루트 [CLAUDE.md](../../CLAUDE.md)
- [tests/CLAUDE.md](../../tests/CLAUDE.md) (테스트 추가)
- 선행 계획서 [PLAN_harness_migration.md](PLAN_harness_migration.md) (훅 설계 결정 이력)

### 확정된 설계 결정

| 항목 | 결정 | 근거 |
| --- | --- | --- |
| Bash 검사 트리거 | 명령문에 `PLAN_<name>.md` 토큰이 **실제로 등장할 때만** 해당 파일 검사 | 전수 스캔은 `ls docs/plans/` 같은 무관한 명령까지 기존 위반으로 차단한다. 토큰 추출은 상태·시간에 의존하지 않아 테스트로 고정 가능하다 |
| Bash 경로의 마커 기록 | **하지 않는다** | Bash는 읽기/쓰기를 구분할 수 없다. `grep PLAN_x.md` 만으로 게이트가 열리면 안 되므로, 게이트가 더 자주 걸리는 쪽(안전한 방향)으로 둔다 |
| 파이썬 표준 이관처 | `.claude/rules/python.md` (`paths`: py 파일들) | 경로 스코프 규칙의 표준 용법. `.md`만 편집할 때 로드되지 않는다 |
| 이관 후 안전장치 | CLAUDE.md에 한 줄 포인터 유지 | path-scoped rule은 "매칭 파일을 읽을 때" 로드된다. 아무것도 읽지 않고 새 `.py`를 만드는 경우를 대비한다 |
| 전역 중복 제거 | 프로젝트에서 제거하고 전역에 위임 | 사용자 승인 완료. 단 저장소 이식성이 낮아지는 트레이드오프가 있다(§7) |

## 4) 완료 조건(Definition of Done)

> Done은 "서술"이 아니라 "체크리스트 상태"로만 판단합니다. (정의/예외는 `/plan` 스킬)

- [x] 기능 요구사항 충족 (목표 1~2)
- [x] 회귀/신규 테스트 추가 (Bash 명령문에서 계획서 토큰 추출)
- [x] Bash 우회 시나리오를 실제 JSON 입력으로 재현·차단 확인
- [x] 루트 `CLAUDE.md` 줄 수가 200줄 미만임을 확인
- [x] `poetry run python validate_project.py` 통과 (failed=0, skipped=0; passed/failed/skipped 수 기록)
- [x] `poetry run black .` 실행 완료 (마지막 Phase에서 자동 포맷 적용)
- [x] 필요한 문서 업데이트(README.md / `docs/COMMANDS.md` / CLAUDE.md / plan 등 — 각각 변경 여부 명시)
- [x] plan 체크박스 최신화(Phase/DoD/Validation 모두 반영)

## 5) 변경 범위(Scope)

### 변경 대상 파일(예상)

신규:

- `.claude/rules/python.md` — 구현 원칙·코딩 표준·로깅 정책 (`paths`로 py 파일 한정)

수정:

- `.claude/hooks/plan_lint.py` — Bash 명령문에서 계획서 파일 토큰을 추출해 검사하는 경로 추가
- `.claude/settings.json` — PostToolUse에 `Bash` matcher 등록
- `tests/test_plan_lint.py` — 토큰 추출 계약 테스트 추가
- `CLAUDE.md` — 전역 중복 섹션 제거, 파이썬 표준을 rule로 이관하고 포인터만 유지

변경 없음(명시):

- `README.md`: **변경 없음** — 코딩 표준이나 훅을 언급하지 않는다
- `docs/COMMANDS.md`: **변경 없음** — 실행 명령어/CLI 옵션이 바뀌지 않는다
- `.claude/hooks/plan_gate.py`: **변경 없음** — 비목표 참고
- `~/.claude/CLAUDE.md` (전역): **변경 없음** — 프로젝트 저장소 밖이다

### 데이터/결과 영향

- 없음. 비즈니스 로직과 `storage/` 산출물에 접근하지 않는다.
- 런타임 영향은 개발 세션의 툴 호출 흐름에 한정된다. Bash로 계획서를 고칠 때 검사가 추가로 실행된다.

## 6) 단계별 계획(Phases)

### Phase 1 — Bash 우회 차단 (그린 유지)

**작업 내용**:

- [x] `plan_lint.py`에 `plans_referenced(command, cwd)` 추가 — 명령문에서 `PLAN_<name>.md` 토큰을 뽑아 실재하는 계획서 경로만 반환
- [x] `main()`에 Bash 분기 추가 — `tool_input.command` 가 있으면 참조된 계획서들을 검사하고 위반 시 `decision: block`
- [x] `.claude/settings.json` PostToolUse에 `Bash` matcher 등록
- [x] `tests/test_plan_lint.py`에 토큰 추출 계약 테스트 추가 (탐지 케이스 / 무관한 명령 / 존재하지 않는 파일)

**Validation**:

- [x] `sed -i` 로 계획서를 고치는 Bash 페이로드를 넣어 `decision: block` 이 나오는지 확인
- [x] `ls docs/plans/`, `git status` 등 무관한 명령이 통과하는지 확인 (기존 위반 파일이 있어도 차단되지 않아야 함)

---

### Phase 2 — 루트 CLAUDE.md 축소 (그린 유지)

**작업 내용**:

- [x] `.claude/rules/python.md` 작성 — "구현 원칙", "코딩 표준", "로깅 정책" 섹션을 원문 그대로 이관
- [x] `CLAUDE.md`에서 위 세 섹션 제거하고 한 줄 포인터로 대체
- [x] `CLAUDE.md`에서 "사고 절차(질문 형식 표준화 포함)", "수술적 변경" 제거 (전역에 위임)
- [x] "규칙 문서 구성" 섹션에 `.claude/rules/python.md` 반영

**Validation**:

- [x] `wc -l CLAUDE.md` 가 200 미만
- [x] 이관된 규칙 문구가 원문과 동일한지 대조 (내용 변경 없음)

---

### 마지막 Phase — 문서 정리 및 최종 검증

**작업 내용**

- [x] 필요한 문서 업데이트 (README.md / `docs/COMMANDS.md` 변경 여부 명시)
- [x] `poetry run black .` 실행(자동 포맷 적용)
- [x] 변경 기능 및 전체 플로우 최종 검증
- [x] DoD 체크리스트 최종 업데이트 및 체크 완료
- [x] 전체 Phase 체크리스트 최종 업데이트 및 상태 확정

**Validation**:

- [x] `poetry run python validate_project.py` (passed=411, failed=0, skipped=0)

#### Commit Messages (Final candidates) — 5개 중 1개 선택

1. 하네스 / Bash 우회 경로 차단 및 루트 CLAUDE.md 축소 (파이썬 표준 rule 이관)
2. 하네스 / plan_lint 에 Bash 분기 추가 + 코딩 표준을 path-scoped rule 로 분리
3. 하네스 / 계획서 검사 사각지대 제거 및 상시 로드 컨텍스트 절감
4. 하네스 / 훅 matcher 확장(Bash) + CLAUDE.md 308→200줄 미만 정리
5. 문서 / 파이썬 코딩 표준을 .claude/rules 로 이관하고 전역 중복 섹션 제거

## 7) 리스크(Risks)

| 리스크 | 영향 | 완화책 |
| --- | --- | --- |
| Bash 검사가 무관한 명령까지 차단 | 작업 흐름 저해 | 명령문에 계획서 파일명이 실제로 등장할 때만 검사. 테스트로 고정 |
| 동적으로 경로를 조립하는 스크립트는 여전히 우회 | 검사 누락 | 비목표로 명시. 실수 방지용 가드레일이지 보안 경계가 아니다 |
| path-scoped rule이 새 `.py` 작성 시 로드되지 않을 수 있음 | 코딩 표준 누락 | CLAUDE.md에 포인터 한 줄 유지 |
| 전역 중복 제거로 저장소 이식성 저하 | 다른 사람/다른 머신에서 사고 절차·수술적 변경 규칙이 빠짐 | 사용자 단독 프로젝트이며 승인된 결정. 필요해지면 프로젝트로 되돌린다 |
| 규칙 이관 중 문구 유실 | 규칙 약화 | 원문 그대로 복사하고 Phase 2 Validation에서 대조 |

## 8) 메모(Notes)

- Bash 분기는 `plan_gate`(PreToolUse)에는 추가하지 않는다. 사전에 "이 명령이 무엇을 쓸지"를 판정할 수 없기 때문이다. 사후 검사만으로도 잘못된 상태가 저장된 채 남는 것은 막을 수 있다.
- 이번 작업도 계획서를 Bash로 고치지 않고 Edit 툴로만 갱신한다.
- Phase 2 이후 파이썬 코딩 표준의 SoT는 `.claude/rules/python.md` 다. 루트 `CLAUDE.md`에는 포인터만 남는다.

### 진행 로그 (KST)

- 2026-07-29 17:09: 계획서 작성. Bash 우회는 선행 plan 진행 중 실제로 발생한 사례를 근거로 확정.
- 2026-07-29 17:18: Phase 1 완료. `plans_referenced()` 로 명령문에서 계획서 토큰만 추출하는 방식 채택. 실측 결과 — `sed -i`·python heredoc 우회는 `block`, `ls docs/plans/`·`git status`·`pytest`·`grep`은 기존 위반 파일이 있어도 통과(오탐 0). Edit/Write 경로 회귀 없음. 테스트 30건 통과.
- 2026-07-29 17:25: Phase 2 완료. `CLAUDE.md` **308줄 → 153줄**(권고 200줄 미만 충족). 이관된 84줄을 `git show HEAD:CLAUDE.md` 원문과 기계 대조해 **전량 일치** 확인(링크 상대경로 보정분 제외). 전역 위임 대상 2개 섹션이 `~/.claude/CLAUDE.md`에 실재함도 확인.
- 2026-07-29 17:28: 마지막 Phase. `black .` 1건 재포맷. `validate_project.py` 통과(passed=411, failed=0, skipped=0). README.md·docs/COMMANDS.md 변경 없음 확인 완료. **Done**
