# docs 폴더 가이드

> 이 문서는 `docs/` 폴더의 문서 작성 및 계획서(Implementation Plan) 운영 규칙(SoT)을 정의합니다.
> 프로젝트 전반의 공통 규칙은 [루트 CLAUDE.md](../CLAUDE.md)를 참고하세요.

## 폴더 목적

`docs/`는 krx-sprint 프로젝트의 개발/운영 문서와 변경 계획서(Implementation Plan)를 관리합니다.

## 폴더 구조

```

docs/
├── CLAUDE.md           # docs 관련 규칙(SoT) (이 문서)
├── plans/              # 변경 계획서 저장소
│   └── _template.md    # 계획서 템플릿(예시/양식)
└── archive/            # 과거 문서 보관소(기본 무시)

```

### docs/archive 정책 (중요)

- `docs/archive`는 과거 문서 보관용입니다.
- 현재 작업의 규칙/근거/소스 오브 트루스(Source of Truth)로 사용하지 않습니다.
- 현행 규칙은 루트 `CLAUDE.md` + 작업 범위 도메인 `CLAUDE.md` + 최신 plan만을 기준으로 합니다.

## 소스 오브 트루스 & 반드시 읽어야 할 문서

> 주의: plan의 "영향받는 규칙"에는 규칙을 요약/나열하지 말고,
> 참고할 문서(파일) 목록만 나열한 뒤
> "해당 문서들에 기재된 규칙을 모두 숙지하고 준수한다"를 명시합니다.

## 날짜/시간 표기 규칙 (KST)

계획서의 메타 정보와 로그에는 일시를 기록합니다.

- 시간대: KST(Asia/Seoul)
- 형식: `YYYY-MM-DD HH:MM`
- 적용 대상: `작성일`, `마지막 업데이트`, `진행 로그`

예시: `2025-12-25 14:30`

## 스크립트 실행 규칙

`scripts/` 폴더의 스크립트 실행에 관한 규칙은 [루트 CLAUDE.md](../CLAUDE.md#스크립트-실행-규칙)를 참고하세요.
AI 모델은 scripts 폴더의 스크립트를 직접 실행하지 않으며, 사용자만이 직접 실행합니다.

## 포맷/린트/테스트 규칙

계획서(Plan) 작성 시:

- `poetry run python validate_project.py`는 마지막 Phase에서만 실행합니다.
- 중간 Phase에서는 실행하지 않습니다.

### Black 실행 원칙

- Black은 마지막 Phase에서 자동 포맷 적용만 수행합니다.
- 마지막 Phase에서 `poetry run black .`를 실행합니다.
- `poetry run black --check .`는 사용하지 않습니다.

## plans 폴더 사용 규칙

### 파일 네이밍

- 계획서는 `docs/plans/PLAN_<short_name>.md` 형태로 생성합니다.
- `<short_name>`은 작업 범위와 목적이 드러나도록 간결히 작성합니다.

## 계획서 운영 규칙(SoT)

### 1) 계획서 필수 구성

- Goal: 목표 설정
- Non-Goals: 범위 제외 항목
- Context: 배경/필요성/영향 받는 규칙 + "전체 숙지" 선언
- Definition of Done: 완료 조건 체크리스트
- Scope: 변경 범위(변경 대상 파일, 데이터/결과 영향)
  - `README.md` 및 `docs/COMMANDS.md` 업데이트 필요 여부를 반드시 각각 명시한다. 불필요하면 각각 `README.md 변경 없음`, `docs/COMMANDS.md 변경 없음`으로 기록한다.
  - `docs/COMMANDS.md` 는 모든 실행 명령어(`poetry run`, `streamlit run` 등)의 단일 SoT 이다. 실행 방법/CLI 옵션이 바뀌면 이 문서도 반드시 함께 갱신한다.
- Phases: 단계별 계획(각 Phase의 할 일 + Validation)
- Risks: 리스크와 완화책
- Notes: 메모/결정사항/링크/진행 로그

### 2) Phase 구성 원칙

- Phase는 "파일 수"가 아니라 문맥(context) 기준으로 구성합니다.
- 한 Phase 안에서 "검증/수정/재검증"이 자연스럽게 닫히는 단위로 묶습니다.
- 핵심 인바리언트/정책을 테스트로 먼저 고정해야 한다면 Phase 0(레드)를 둡니다.
- Phase 1부터는 그린(테스트 통과 상태) 유지가 원칙입니다.

### 3) 스킵(Skipped) 및 완료(Done) 규칙

#### 스킵 설계 원칙: 스킵이 "존재하지 않게" 설계한다

스킵은 "아직 구현이 없어서 테스트를 못 만든다"를 의미하는 경우가 많습니다.
이는 스킵이 아니라 Phase 분해로 해결합니다.

- Phase 0: 만들 수 있는 테스트(정책/인터페이스/불변조건)부터 최대한 작성
- Phase 1: 필요한 함수/로직 구현으로 Phase 0 테스트 통과
- Phase 2: 부족했던 테스트 추가로 커버리지 완성

즉, 테스트를 스킵으로 미루지 말고 Phase를 나누어 "테스트+구현"으로 완성합니다.

#### Done 선언 조건 (체크리스트 기반, 서술 금지)

Done은 "말/요약"이 아니라 plan의 체크리스트 상태로만 판단합니다.
아래 조건을 모두 만족할 때만 `상태: Done`으로 표기할 수 있습니다.

1. Definition of Done(DoD) 체크리스트가 모두 [x]
2. 마지막 Validation의 `poetry run python validate_project.py` 결과가 `failed=0` 그리고 `skipped=0`
3. plan 내에 "미완료([ ]) 항목"이 남아있지 않음(Phase/DoD/필수 체크 포함)

#### 스킵이 남아있는 경우 (불가피한 예외)

스킵이 정말 불가피하면 허용할 수 있으나, skipped > 0이면:

- plan 상태를 Done으로 처리할 수 없습니다.
- DoD 체크박스(특히 테스트/Validation 관련)를 [x]로 처리하면 안 됩니다.
- Validation 결과에는 반드시 `passed/failed/skipped` 수를 기록합니다.
- Notes에 스킵 사유/해제 조건/후속 plan 계획을 반드시 기록합니다.

> 핵심: `skipped > 0` 인데도 `Done` 또는 DoD의 테스트/검증 항목이 [x]로 표시되는 일이 없도록,
> "상태/체크박스/Validation 숫자"가 서로 모순되지 않게 기계적으로 맞춥니다.

### 4) Commit Messages 규칙

#### 기본 원칙

- Commit Messages는 "실제로 수행하는 변경" 기준(추정 금지).
- 형식은 `기능명 / ...` 형태 권장.

#### 어디에 써야 하는가 (기본값)

- Phase별 Commit Messages는 기본적으로 작성하지 않습니다.
- plan의 마지막(완료 직전/완료 지점)에만 `Commit Messages (Final candidates)`를 둡니다.
- `Commit Messages (Final candidates)`에는 5개 후보를 제시합니다.
  - 사용자가 그중 1개를 선택해서 실제 커밋 메시지로 사용합니다.

#### 예외 (사용자 요청이 있는 경우)

- 사용자가 "중간 Phase 커밋 단위 분리"를 명시적으로 요청하면,
  해당 Phase에 한해 `Commit Messages (Phase N)`을 추가할 수 있습니다.

#### 파일 경로 기반 기능명 추천(권장)

- 데이터 수집 로직 변경: `수집 / `
- 품질 검증 로직 변경: `검증 / `
- 문서 변경: `문서 / `
- 그 외는 변경 내용을 보고 적절한 기능명을 선택합니다.
