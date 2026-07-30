---
name: plan
description: krx-sprint 계획서(Implementation Plan) 작성·갱신 절차의 SoT. 코드 변경 전 docs/plans/PLAN_<short_name>.md 를 만들 때, 기존 계획서의 Phase/체크리스트를 갱신할 때, Done 처리 조건을 판단할 때 사용한다.
when_to_use: 사용자가 계획서·plan 작성을 요청할 때. src/·scripts/·tests/ 의 코드를 변경하기 전. docs/plans/ 아래 파일을 읽거나 수정할 때. plan 을 Done 으로 표기해도 되는지 판단할 때.
argument-hint: [short_name]
paths:
  - "docs/plans/**"
---

# 계획서(Implementation Plan) 작성 절차

krx-sprint의 모든 코드 변경은 계획서를 먼저 작성한 뒤 진행한다.
예외는 오타 수정, 주석 수정, 로그 메시지 수정뿐이다.

## 파일 위치와 네이밍

- 경로: `docs/plans/PLAN_<short_name>.md`
- `<short_name>`은 작업 범위와 목적이 드러나도록 간결히 작성한다.
- 템플릿: 이 스킬 폴더의 `template.md`. 새 계획서는 이 파일을 복사해서 시작한다.

## 날짜/시간 표기 (KST)

- 시간대: KST(Asia/Seoul)
- 형식: `YYYY-MM-DD HH:MM` (예: `2025-12-25 14:30`)
- 적용 대상: `작성일`, `마지막 업데이트`, `진행 로그`
- 현재 시각은 추정하지 말고 `TZ=Asia/Seoul date '+%Y-%m-%d %H:%M'` 으로 확인한다.

## 1) 계획서 필수 구성

- **Goal**: 목표 설정
- **Non-Goals**: 범위 제외 항목
- **Context**: 배경/필요성/영향받는 규칙 + "전체 숙지" 선언
- **Definition of Done**: 완료 조건 체크리스트
- **Scope**: 변경 범위(변경 대상 파일, 데이터/결과 영향)
  - `docs/COMMANDS.md` 업데이트 필요 여부를 반드시 명시한다. 불필요하면 `docs/COMMANDS.md 변경 없음`으로 기록한다.
  - `docs/COMMANDS.md`는 모든 실행 명령어(`poetry run`, `streamlit run` 등)의 단일 SoT다. 실행 방법/CLI 옵션이 바뀌면 반드시 함께 갱신한다.
- **Phases**: 단계별 계획(각 Phase의 할 일 + Validation)
- **Risks**: 리스크와 완화책
- **Notes**: 메모/결정사항/링크/진행 로그

> 주의: Context의 "영향받는 규칙"에는 규칙을 요약·나열하지 말고,
> 참고할 문서(파일) 목록만 나열한 뒤
> "해당 문서들에 기재된 규칙을 모두 숙지하고 준수한다"를 명시한다.

## 2) Phase 구성 원칙

- Phase는 "파일 수"가 아니라 **문맥(context) 기준**으로 구성한다.
- 한 Phase 안에서 "검증/수정/재검증"이 자연스럽게 닫히는 단위로 묶는다.
- 핵심 인바리언트/정책을 테스트로 먼저 고정해야 한다면 Phase 0(레드)를 둔다.
- Phase 1부터는 그린(테스트 통과 상태) 유지가 원칙이다.

### 포맷/린트/테스트 실행 타이밍

- `poetry run python validate_project.py`는 **마지막 Phase에서만** 실행한다. 중간 Phase에서는 실행하지 않는다.
- Black은 마지막 Phase에서 `poetry run black .` 으로 **자동 포맷 적용만** 수행한다. `poetry run black --check .`는 사용하지 않는다.

## 3) 스킵(Skipped) 및 완료(Done) 규칙

### 스킵이 "존재하지 않게" 설계한다

스킵은 "아직 구현이 없어서 테스트를 못 만든다"를 의미하는 경우가 많다.
이는 스킵이 아니라 **Phase 분해**로 해결한다.

- Phase 0: 만들 수 있는 테스트(정책/인터페이스/불변조건)부터 최대한 작성
- Phase 1: 필요한 함수/로직 구현으로 Phase 0 테스트 통과
- Phase 2: 부족했던 테스트 추가로 커버리지 완성

즉, 테스트를 스킵으로 미루지 말고 Phase를 나누어 "테스트+구현"으로 완성한다.

### Done 선언 조건 (체크리스트 기반, 서술 금지)

Done은 "말/요약"이 아니라 plan의 **체크리스트 상태로만** 판단한다.
아래를 모두 만족할 때만 `상태: ✅ Done`으로 표기할 수 있다.

1. Definition of Done(DoD) 체크리스트가 모두 `[x]`
2. 마지막 Validation의 `validate_project.py` 결과가 `failed=0` 그리고 `skipped=0`
3. plan 내에 미완료(`- [ ]`) 항목이 남아있지 않음 (Phase/DoD/필수 체크 포함)

> 이 세 조건은 `.claude/hooks/plan_lint.py`가 PLAN 파일 저장 시 기계적으로 검사한다.
> 위반 상태로는 저장이 차단되므로, 규칙을 기억에 의존해 맞추지 않아도 된다.

### 근거 승격 (Done 전 필수, 훅이 검사하지 않음)

`docs/plans/`는 **임시 산출물**이다. 사용자가 주기적으로 전부 삭제하므로,
계획서에만 있는 정보는 그때 사라진다. Done 처리 전에 **남길 가치가 있는 것을 살아있는 문서로 옮긴다.**

- 옮기는 것은 결론뿐 아니라 **근거**다. "값이 17:00이다"가 아니라 **"왜 17:00인가"**를 남긴다.
  근거를 버리면 나중에 같은 논의를 반복하고, 같은 함정을 다시 밟는다
- 목적지는 [docs/ROADMAP.md](../../../docs/ROADMAP.md) "계획서의 수명"이 SoT다 —
  실측·검증 수치는 스펙, 전략 결정과 탈락안은 설계 문서, Phase 상태·설계 계약은 ROADMAP,
  실행 명령어는 `docs/COMMANDS.md`
- **살아있는 문서에서 계획서를 링크하지 않는다.** 링크는 다음 삭제 주기에 깨진다.
  내용을 복사해 자립시킨다
- 판단 기준: **"이 계획서를 지금 지워도 잃는 것이 없는가?"** 아니라면 아직 승격이 안 끝난 것이다

이 항목은 훅이 검사하지 못한다(무엇이 "남길 가치가 있는지"는 기계가 판정할 수 없다).
DoD 체크리스트에 명시적으로 넣어 빠뜨리지 않게 한다.

### 스킵이 남아있는 경우 (불가피한 예외)

스킵이 정말 불가피하면 허용할 수 있으나, `skipped > 0`이면:

- plan 상태를 Done으로 처리할 수 없다.
- DoD 체크박스(특히 테스트/Validation 관련)를 `[x]`로 처리하면 안 된다.
- Validation 결과에는 반드시 `passed/failed/skipped` 수를 기록한다.
- Notes에 스킵 사유/해제 조건/후속 plan 계획을 반드시 기록한다.

## 4) 이미 생성된 계획서 수정 규칙

이미 생성된 plan은 **체크리스트 업데이트와 진행 로그 추가 외에는 수정 금지**한다.
특히 템플릿의 `## 0) 고정 규칙` 섹션은 문구까지 그대로 보존한다.

## 5) Commit Messages

- Commit Messages는 "실제로 수행하는 변경" 기준으로 쓴다(추정 금지).
- 형식은 `기능명 / ...` 를 권장한다.
- Phase별 Commit Messages는 기본적으로 작성하지 않는다. plan의 마지막에만
  `Commit Messages (Final candidates)` 5개 후보를 두고, 사용자가 1개를 선택한다.
- 사용자가 "중간 Phase 커밋 단위 분리"를 명시적으로 요청한 경우에만
  해당 Phase에 `Commit Messages (Phase N)`을 추가할 수 있다.

기능명은 변경된 파일 경로를 기준으로 정한다 (데이터 수집 로직 → `수집 / `,
품질 검증 로직 → `검증 / `, 문서 → `문서 / `, 그 외는 변경 내용에 맞게 선택).

실제 커밋 메시지 후보 생성은 `/commit` 스킬을 사용한다.
