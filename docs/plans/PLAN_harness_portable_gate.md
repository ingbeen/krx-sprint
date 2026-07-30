# Implementation Plan: 계획서 게이트를 언어 중립·이식 가능하게 전환

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

**작성일**: 2026-07-29 18:44
**마지막 업데이트**: 2026-07-29 18:57
**관련 범위**: 하네스 설정 (.claude/hooks/)
**관련 문서**: [CLAUDE.md](../../CLAUDE.md), [tests/CLAUDE.md](../../tests/CLAUDE.md), [PLAN_harness_hardening.md](PLAN_harness_hardening.md)

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

- [x] 목표 1: 게이트 판정을 "`src`·`scripts`·`tests` 아래 `*.py`" 화이트리스트에서 **제외 목록 방식**으로 뒤집어, 언어·레이아웃에 무관하게 동작시킨다.
- [x] 목표 2: 두 훅에 **자기 게이팅**을 넣어, `docs/plans/` 규약을 쓰지 않는 프로젝트에서는 아무 동작도 하지 않게 한다. 전역(`~/.claude/`) 이동 시 훅 수정이 불필요해진다.

## 2) 비목표(Non-Goals)

- 실제 전역 이동. 이번에는 **이동 가능한 상태로 만들기만** 한다. 배치는 프로젝트 범위 그대로 둔다.
- 변경 규모(줄 수) 기반 판정 도입. 사용자 선택으로 제외 목록 방식만 채택했다(§3).
- `plan_gate`의 Bash 대응. 선행 계획서의 비목표를 그대로 승계한다.
- 계획서 템플릿·Done 조건 규칙 자체의 변경.

## 3) 배경/맥락(Context)

### 현재 문제점 / 동기

[PLAN_harness_hardening.md](PLAN_harness_hardening.md)까지 완료된 시점의 게이트 판정은 다음과 같다.

```python
GATED_DIRS = ("src/", "scripts/", "tests/")
GATED_SUFFIX = ".py"
```

이 화이트리스트에는 두 가지 문제가 있다.

**1. 언어·레이아웃 종속** — Java·TypeScript 프로젝트에 그대로 넣으면 아무것도 걸리지 않는다.
같은 파이썬 프로젝트 안에서도 `pyproject.toml`, `pyrightconfig.json`, 루트의 `validate_project.py`는
게이트 밖이다. 의존성이나 타입 체커 설정 변경은 영향도가 낮지 않다.

**2. 화이트리스트는 누락 쪽으로 실패한다** — 새 언어나 새 빌드 파일이 생길 때마다 목록을 고쳐야 하고,
고치지 않으면 조용히 통과한다. 게이트의 목적(놓치지 않기)과 실패 방향이 반대다.

**포터빌리티의 진짜 걸림돌은 확장자가 아니다.** 훅에는 그 밖에도 프로젝트 종속 요소가 있다.

| 요소 | 종속성 |
| --- | --- |
| `src/`·`scripts/`·`tests/` + `.py` | 언어·레이아웃 |
| `docs/plans/PLAN_*.md` 경로 규약 | 프로젝트 |
| `**상태**: ✅ Done`, `## 0) 고정 규칙` | 템플릿 |
| `validate_project.py`의 `failed=0, skipped=0` | 도구 |

확장자만 고치면 `docs/plans/`가 없는 프로젝트에서 게이트가 **영원히 걸린다**. 자기 게이팅이 함께 필요하다.

### 영향받는 규칙(반드시 읽고 전체 숙지)

> 아래 문서에 기재된 규칙을 **모두 숙지**하고 준수합니다.

- 루트 [CLAUDE.md](../../CLAUDE.md)
- [.claude/rules/python.md](../../.claude/rules/python.md)
- [tests/CLAUDE.md](../../tests/CLAUDE.md)
- 선행 계획서 [PLAN_harness_hardening.md](PLAN_harness_hardening.md), [PLAN_harness_migration.md](PLAN_harness_migration.md)

### 확정된 설계 결정

| 항목 | 결정 | 근거 |
| --- | --- | --- |
| 판정 방식 | **제외 목록**(`docs/**`, `**/*.md`) 외 전부 게이트 | 사용자 선택. 화이트리스트와 달리 **과탐 쪽으로 실패**하므로 게이트 목적에 맞다 |
| 오탐 허용 근거 | 게이트는 `deny`가 아니라 **`ask`이고 세션당 1회** | 오탐 비용이 키 입력 한 번이다. 비용 비대칭이 넓은 그물을 정당화한다 |
| `docs/**` 제외 | 필수 | 계획서가 `docs/plans/`에 있어 게이트하면 순환(계획서를 쓰려면 계획서가 필요)이 생긴다. 문서는 규칙상 "코드 변경"이 아니다 |
| `**/*.md` 제외 | 루트 README·각 폴더 CLAUDE.md 대응 | 위와 같은 이유 |
| 프로젝트 밖 경로 | 게이트 대상 아님 | `relative_path`가 `../`로 시작하면 이 프로젝트 파일이 아니다 |
| `.claude/**` | **게이트 대상에 포함** | 훅·설정도 코드다. 훅이 깨져도 훅 실패는 non-blocking이라 잠금 위험이 없다 |
| 자기 게이팅 판정 | `<cwd>/docs/plans/` 디렉토리 존재 여부 | 규약 채택 여부를 나타내는 가장 단순한 신호. 파일 내용에 의존하지 않는다 |
| 변경 규모 기반 판정 | **도입하지 않음** | 1줄 상수 변경(`FEE_RATE = 0.015 → 0.15`)처럼 영향도와 줄 수가 비례하지 않는다. `ask`가 이미 사용자 판단을 받으므로 이득이 적다 |

## 4) 완료 조건(Definition of Done)

> Done은 "서술"이 아니라 "체크리스트 상태"로만 판단합니다. (정의/예외는 `/plan` 스킬)

- [x] 기능 요구사항 충족 (목표 1~2)
- [x] 회귀/신규 테스트 추가 (제외 목록 판정, 자기 게이팅)
- [x] 언어 중립 동작을 실제 JSON 입력으로 확인 (`.java`/`.ts`/빌드 파일이 게이트에 걸림)
- [x] 자기 게이팅을 임시 디렉토리로 확인 (`docs/plans/` 없는 프로젝트에서 무동작)
- [x] `poetry run python validate_project.py` 통과 (failed=0, skipped=0; passed/failed/skipped 수 기록)
- [x] `poetry run black .` 실행 완료 (마지막 Phase에서 자동 포맷 적용)
- [x] 필요한 문서 업데이트(README.md / `docs/COMMANDS.md` / CLAUDE.md / plan 등 — 각각 변경 여부 명시)
- [x] plan 체크박스 최신화(Phase/DoD/Validation 모두 반영)

## 5) 변경 범위(Scope)

### 변경 대상 파일(예상)

수정:

- `.claude/hooks/plan_lint.py` — `is_gated` 를 제외 목록 방식으로 전환, `project_uses_plans` 추가 및 `main()` 선두 적용, 상수 개명(`GATED_DIRS`/`GATED_SUFFIX` → `EXCLUDED_DIRS`/`EXCLUDED_SUFFIXES`)
- `.claude/hooks/plan_gate.py` — `project_uses_plans` 적용, 안내 문구에서 `*.py` 한정 표현 제거
- `tests/test_plan_lint.py` — `TestGatedPaths` 재작성(기존 기대값이 뒤집힘), 자기 게이팅 테스트 추가
- `CLAUDE.md` — 계획서 섹션의 "`src/`·`scripts/`·`tests/` 아래 `*.py` 편집 시" 문구를 실제 동작에 맞게 갱신

변경 없음(명시):

- `README.md`: **변경 없음** — 훅을 언급하지 않는다
- `docs/COMMANDS.md`: **변경 없음** — 실행 명령어/CLI 옵션이 바뀌지 않는다
- `.claude/settings.json`: **변경 없음** — matcher는 그대로다. 판정은 훅 스크립트 안에서만 바뀐다
- `.claude/skills/plan/`: **변경 없음** — 계획서 절차 규칙은 그대로다
- 완료된 `PLAN_*.md`: **수정 금지** (섹션 0)

### 데이터/결과 영향

- 없음. 비즈니스 로직과 `storage/` 산출물에 접근하지 않는다.
- 런타임 영향: 게이트 대상이 넓어져 `.md`·`docs/` 외 파일의 세션 첫 편집에서 프롬프트가 뜬다. 세션당 1회 제한은 그대로다.

## 6) 단계별 계획(Phases)

### Phase 1 — 제외 목록 방식 전환 및 자기 게이팅 (그린 유지)

**작업 내용**:

- [x] `plan_lint.py`: `EXCLUDED_DIRS`/`EXCLUDED_SUFFIXES` 상수 도입, `is_gated` 를 제외 판정으로 재작성 (프로젝트 밖 `../` 경로 포함)
- [x] `plan_lint.py`: `project_uses_plans(cwd)` 추가 후 `main()` 선두에서 미채택 프로젝트를 즉시 통과
- [x] `plan_gate.py`: 동일한 자기 게이팅 적용, `GATE_REASON` 에서 `*.py` 한정 문구 제거
- [x] `tests/test_plan_lint.py`: `TestGatedPaths` 를 새 계약으로 재작성하고 `TestProjectUsesPlans` 추가

**Validation**:

- [x] `.java`/`.ts`/`pom.xml`/`pyproject.toml` 경로가 게이트에 걸리는지 JSON 입력으로 확인
- [x] `docs/**`·`**/*.md` 가 통과하는지 확인
- [x] `docs/plans/` 없는 임시 디렉토리를 cwd 로 주면 두 훅 모두 무동작인지 확인

---

### Phase 2 — 문서 갱신 (그린 유지)

**작업 내용**:

- [x] `CLAUDE.md` 계획서 섹션의 하네스 강제 설명을 새 판정 기준으로 갱신

**Validation**:

- [x] `grep -n "\*.py" CLAUDE.md` 결과에 게이트 관련 낡은 문구가 남지 않음

---

### 마지막 Phase — 문서 정리 및 최종 검증

**작업 내용**

- [x] 필요한 문서 업데이트 (README.md / `docs/COMMANDS.md` 변경 여부 명시)
- [x] `poetry run black .` 실행(자동 포맷 적용)
- [x] 변경 기능 및 전체 플로우 최종 검증
- [x] DoD 체크리스트 최종 업데이트 및 체크 완료
- [x] 전체 Phase 체크리스트 최종 업데이트 및 상태 확정

**Validation**:

- [x] `poetry run python validate_project.py` (passed=425, failed=0, skipped=0)

#### Commit Messages (Final candidates) — 5개 중 1개 선택

1. 하네스 / 계획서 게이트를 제외 목록 방식으로 전환하고 자기 게이팅 추가
2. 하네스 / 게이트 판정에서 언어 종속 제거 — 전역 이동 가능한 형태로 정리
3. 하네스 / `*.py` 화이트리스트를 제외 목록으로 뒤집어 누락 방향 실패 차단
4. 하네스 / docs·md 외 전체를 게이트 대상으로 확대, 규약 미채택 프로젝트는 무동작
5. 하네스 / plan_gate·plan_lint 이식성 확보 (판정 기준 반전 + docs/plans 자기 게이팅)

## 7) 리스크(Risks)

| 리스크 | 영향 | 완화책 |
| --- | --- | --- |
| 게이트 범위 확대로 프롬프트 빈도 증가 | 작업 흐름 저해 | `ask` + 세션당 1회 유지. `docs/**`·`**/*.md` 제외로 문서 작업은 영향 없음 |
| 데이터 파일(`storage/**`) 편집도 게이트 대상이 됨 | 프롬프트 1회 추가 | 의도된 동작이다. 데이터 파일 직접 편집은 영향도가 낮지 않다 |
| 자기 게이팅 판정이 너무 느슨함 (빈 `docs/plans/` 폴더만 있어도 활성) | 규약 미채택 프로젝트에서 오동작 | 폴더를 만든 것 자체가 채택 의사다. 내용 검사는 과설계 |
| 기존 테스트 기대값이 뒤집혀 회귀로 오인 | 혼란 | 테스트를 새 계약으로 재작성하고 각 케이스에 이유를 docstring 으로 남긴다 |
| 훅 파일 자신이 게이트 대상이 됨 | 훅 수정 시 프롬프트 | 의도된 동작. 훅 실행 실패는 non-blocking 이라 잠금 위험 없음 |

## 8) 메모(Notes)

- 판정 방식과 자기 게이팅 도입 여부는 사용자가 선택했다(제외 목록 방식 / 함께 넣기).
- 이번 변경으로 훅에서 제거되는 종속성은 "언어·레이아웃"과 "규약 미채택 프로젝트"다. 템플릿 종속(`**상태**: ✅ Done` 등)과 도구 종속(`validate_project.py`)은 그대로 남으며, 전역 이동 시에도 `docs/plans/` 규약을 채택한 프로젝트에서만 동작하므로 문제되지 않는다.
- `.claude/settings.json` 의 matcher(`Edit|Write`, `Edit|Write|Bash`)는 건드리지 않는다. 판정은 전부 훅 스크립트 안에 있다.

### 진행 로그 (KST)

- 2026-07-29 18:44: 계획서 작성. 화이트리스트가 "누락 방향으로 실패"한다는 점과, 확장자만 고쳐서는 이식이 불가능하다는 점을 근거로 제외 목록 + 자기 게이팅 조합을 확정.
- 2026-07-29 18:52: Phase 1 완료. `is_gated` 반전, `project_uses_plans` 추가, 두 훅 `main()` 선두 적용. 실측 — `App.java`·`index.ts`·`pom.xml`·`pyproject.toml`·루트 `validate_project.py` 모두 `ask` 발동, 문서 4종 통과. 자기 게이팅은 `docs/` 만 있는 경우까지 무동작이고 `docs/plans/` 생성 직후 발동함을 임시 디렉토리로 확인. 기존 계획서 9건 block 0건.
- 2026-07-29 18:55: Phase 2 완료. `CLAUDE.md` 하네스 강제 설명을 새 판정 기준으로 갱신. `grep '\*\.py'` 결과 잔여 문구 없음.
- 2026-07-29 18:57: 마지막 Phase. `black .` 재포맷 0건. `validate_project.py` 통과(passed=425, failed=0, skipped=0 — 테스트 14건 증가). README.md·docs/COMMANDS.md 변경 없음 확인 완료. **Done**
