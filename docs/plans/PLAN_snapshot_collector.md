# Implementation Plan: 1단 전종목 스냅샷 수집기 (ROADMAP Phase 2)

> 작성/운영 규칙(SoT): 반드시 [docs/CLAUDE.md](../CLAUDE.md)를 참고하세요.  
> (이 템플릿을 수정하거나 새로운 양식의 계획서를 만들 때도 [docs/CLAUDE.md](../CLAUDE.md)를 포인터로 두고 준수합니다.)

**상태**: ✅ Done

---

🚫 **이 영역은 삭제/수정 금지** 🚫

**상태 옵션**: 🟡 Draft / 🔄 In Progress / ✅ Done

**Done 처리 규칙**:

- ✅ Done 조건: DoD 모두 [x] + `skipped=0` + `failed=0`
- ⚠️ **스킵이 1개라도 존재하면 Done 처리 금지 + DoD 테스트 항목 체크 금지**
- 상세: [docs/CLAUDE.md](../CLAUDE.md) 섹션 3, 5 참고

---

**작성일**: 2026-07-27 10:16
**마지막 업데이트**: 2026-07-27 11:05
**관련 범위**: collect, scripts/data, tests, docs
**관련 문서**: [scripts/CLAUDE.md](../../scripts/CLAUDE.md), [tests/CLAUDE.md](../../tests/CLAUDE.md), [docs/CLAUDE.md](../CLAUDE.md), [docs/데이터수집_스펙_v2.md](../데이터수집_스펙_v2.md), [docs/ROADMAP.md](../ROADMAP.md), [docs/plans/PLAN_pykrx_gate.md](PLAN_pykrx_gate.md)

---

## 0) 고정 규칙 (이 plan은 반드시 아래 규칙을 따른다)

> 🚫 **이 영역은 삭제/수정 금지** 🚫
> 이 섹션(0)은 지워지면 안 될 뿐만 아니라 **문구가 수정되면 안 됩니다.**
> 규칙의 상세 정의/예외는 반드시 [docs/CLAUDE.md](../CLAUDE.md)를 따릅니다.

- `poetry run python validate_project.py`는 **마지막 Phase에서만 실행**한다. 실패하면 즉시 수정 후 재검증한다.
- Phase 0은 "레드(의도적 실패 테스트)" 허용, Phase 1부터는 **그린 유지**를 원칙으로 한다.
- 이미 생성된 plan은 **체크리스트 업데이트 외 수정 금지**한다.
- 스킵은 가능하면 **Phase 분해로 제거**한다.

---

## 1) 목표(Goal)

- [x] 목표 1: 단일 일자의 KOSPI·KOSDAQ 전종목 스냅샷을 스펙 §7.2 스키마로 만들어 `storage/snapshots/{YYYY}/{YYYYMMDD}.parquet`에 저장한다
- [x] 목표 2: 누락 일자만 골라 수집하는 백필 루프를 만든다 — 파일 존재/휴장/실패를 구분하고, 중단 후 재실행하면 이어서 진행된다 (스펙 §7.3)
- [x] 목표 3: 미확정(장중) 데이터가 불변 parquet에 저장되지 않도록 **당일 수집에 시각 게이트**를 건다 (PLAN_pykrx_gate 실측 결과 반영)
- [x] 목표 4: 수집·검증 규칙을 pykrx 없이 검증 가능한 형태로 분리하고 스텁 기반 테스트로 계약을 고정한다
- [x] 목표 5: 2019년 1월 시범 수집으로 영업일 커버리지·휴장 판정·스키마를 실측 확인한다

## 2) 비목표(Non-Goals)

- **전체 백필 실행**(2019-01-01~현재, 약 6,800회 호출) — 사용자가 별도로 실행한다. 이 plan은 시범 수집(2019년 1월)까지만 검증한다
- 2단 수정주가 수집·스크리닝 (ROADMAP Phase 5)
- 품질 검증 리포트 자동화 (ROADMAP Phase 3, 스펙 §8) — 이 plan은 **저장 전 인라인 검증**만 담당하고 사후 리포트는 만들지 않는다
- 증분 업데이트 자동화·git 커밋 연동 (ROADMAP Phase 4)
- **PIT(과거 시점) 종목명 수집** — `names.csv`는 최신 일자 기준 1회 갱신만 한다 (사용자 결정). 일자별 종목명 축적은 비용(호출 +50%) 대비 필요성이 확인되면 별도 plan으로 분리한다
- 병렬 수집 — 레이트리밋 때문에 순차가 원칙 (스펙 부록 A.3)

## 3) 배경/맥락(Context)

### 현재 문제점 / 동기

- 검증 게이트(PLAN_pykrx_gate)가 모두 통과해 스펙 §3.2의 생존편향 방지 설계가 성립함이 확인됐다. 수집기 본체를 만들 조건이 갖춰졌다.
- 게이트 실측에서 **당일 데이터가 장중에도 반환된다**는 사실이 확인됐다(월요일 10:03 KST에 당일 943종목). "조회 성공 = 확정"으로 판단하면 미확정 종가가 불변 parquet에 박히고, 스펙 §7.1의 불변 파일 계약과 충돌한다. 수집기 설계에 반드시 반영해야 한다.
- pykrx는 KRX 로그인이 필수이며 세션이 1시간 만에 만료된다. 백필은 2시간 이상 걸리므로 수집 도중 재로그인이 발생한다.

### 영향받는 규칙(반드시 읽고 전체 숙지)

> 아래 문서에 기재된 규칙을 **모두 숙지**하고 준수합니다.

- [CLAUDE.md](../../CLAUDE.md) (루트)
- [scripts/CLAUDE.md](../../scripts/CLAUDE.md)
- [tests/CLAUDE.md](../../tests/CLAUDE.md)
- [docs/CLAUDE.md](../CLAUDE.md)
- [docs/데이터수집_스펙_v2.md](../데이터수집_스펙_v2.md)
- [docs/ROADMAP.md](../ROADMAP.md)

## 4) 완료 조건(Definition of Done)

> Done은 "서술"이 아니라 "체크리스트 상태"로만 판단합니다. (정의/예외는 docs/CLAUDE.md)

- [x] 단일 일자 수집·검증·저장이 동작하고 스펙 §7.2 스키마를 만족함
- [x] 백필 루프가 누락 일자만 수집하고, 중단 후 재실행 시 이어서 진행됨
- [x] 당일 시각 게이트가 동작함 (확정 시각 이전에는 오늘을 수집하지 않음)
- [x] 스텁 기반 테스트 추가 (네트워크 호출 없음, `storage/` 실경로 접근 없음)
- [x] 2019년 1월 시범 수집 결과 확인 (사용자 실행)
- [x] `poetry run python validate_project.py` 통과 (passed=99, failed=0, skipped=0)
- [x] `poetry run black .` 실행 완료 (마지막 Phase에서 자동 포맷 적용)
- [x] 필요한 문서 업데이트(README.md / `docs/COMMANDS.md` / CLAUDE.md / plan 등 — 각각 변경 여부 명시)
- [x] plan 체크박스 최신화(Phase/DoD/Validation 모두 반영)

## 5) 변경 범위(Scope)

### 변경 대상 파일(예상)

- `src/krx_sprint/common_constants.py` (수정) — 확정 시각·요청 지연·재시도 상수 추가
- `src/krx_sprint/collect/snapshot.py` (신규) — 조회 결과 → 스키마 변환·검증
- `src/krx_sprint/collect/calendar.py` (신규) — 수집 대상 일자 판정 (시각 게이트·주말·기수집·휴장 제외)
- `src/krx_sprint/collect/snapshot_store.py` (신규) — parquet 경로 규칙·저장(불변 계약)·기수집 판정
- `src/krx_sprint/collect/meta_store.py` (신규) — `holidays.json`·`failures.json` 읽기/쓰기
- `src/krx_sprint/collect/backfill.py` (신규) — 백필 루프(재시도·지연), 조회 함수는 주입받는다
- `src/krx_sprint/collect/names.py` (신규) — `names.csv` 병합
- `scripts/data/collect_snapshots.py` (신규) — pykrx 조회 함수 주입 + 진행 상황 출력 + 메타 기록
- `tests/test_snapshot.py`, `tests/test_calendar.py`, `tests/test_snapshot_store.py`, `tests/test_backfill.py` (신규)
- `docs/COMMANDS.md`: **변경 있음** — 수집 스크립트 실행 명령어 추가
- `scripts/CLAUDE.md`: **변경 있음** — 메타데이터 지원 타입 추가
- `README.md`: **변경 없음** (저장소에 README.md가 없으며 이 plan에서 생성하지 않는다)

### 데이터/결과 영향

- `storage/snapshots/{YYYY}/{YYYYMMDD}.parquet` 신규 생성 (시범 수집 범위: 2019년 1월)
- `storage/meta/holidays.json`·`failures.json` 생성
- `storage/names.csv` 생성
- `storage/meta/meta.json`에 실행 이력 추가
- 과거 일자 파일은 **재작성하지 않는다** (스펙 §7.1 불변 계약)

## 6) 단계별 계획(Phases)

### Phase 0 — 스키마·검증·일자 판정 정책을 테스트로 먼저 고정(레드)

> 해당 사유: 저장 스키마와 "무엇을 이상치로 볼 것인가"는 이후 모든 분석의 전제이며, 잘못 저장되면 불변 파일 계약 때문에 되돌리기 어렵다.

**작업 내용**:

- [x] 스냅샷 스키마 계약을 고정한다 — 컬럼 순서(`SNAPSHOT_COLUMNS`), dtype(가격·거래량·시총·상장주식수 int64 / 등락률 float64), **티커 6자리 문자열 보존(선행 0)**
- [x] 저장 전 검증 정책을 고정한다
  - 두 조회 결과(OHLCV·시가총액)의 티커 집합 불일치 → ValueError
  - 두 결과의 종가 불일치 → ValueError (조회 시점 어긋남 감지)
  - 거래량 0 + OHL 0 → **거래정지(정상)**, 통과시킨다
  - 거래량 > 0 인데 가격 0 이하 → ValueError (진짜 이상치)
  - 고가 < 저가 → ValueError
  - 등락률 절대값이 상하한폭(30%)을 넘으면 → 경고 로그 후 저장 (권리락 등 특이일, 스펙 §8)
  - 빈 조회 결과는 "종목 없음"이 아니라 **휴장 후보**로 구분한다
- [x] 일자 판정 규칙을 고정한다
  - 당일 시각 게이트: 확정 시각(KST) 이전이면 오늘을 후보에서 제외
  - 주말 제외, 기수집 파일 존재 제외, `holidays.json` 기록 제외
  - 수집 시작일(`COLLECTION_START_DATE`) 이전 일자 요청 → ValueError
- [x] 위 정책에 대한 테스트를 최대한 먼저 작성한다 (레드 허용, 시간 고정은 freezegun, 파일은 `tmp_path`)

---

### Phase 1 — 스키마 변환·검증 구현(그린 유지)

**작업 내용**:

- [x] `common_constants.py`에 상수 추가 (확정 시각·요청 지연·최대 재시도 횟수·등락률 상하한폭)
- [x] `snapshot.py` 구현 — 두 조회 결과를 티커로 조인, `market` 라벨 부여, 컬럼 rename(한글→영문), dtype 캐스팅, 검증
- [x] 원본 DataFrame을 변경하지 않는다 (루트 CLAUDE.md 데이터 불변성)
- [x] Phase 0 테스트를 통과시킨다

---

### Phase 2 — 일자 판정·저장·메타 구현(그린 유지)

**작업 내용**:

- [x] `calendar.py` 구현 — 수집 대상 일자 목록 산출 (시각 게이트·주말·기수집·휴장 제외)
- [x] `snapshot_store.py` 구현 — 경로 규칙(`{YYYY}/{YYYYMMDD}.parquet`), 저장 시 기존 파일이 있으면 덮어쓰지 않고 예외, 기수집 일자 집합 조회
- [x] `meta_store.py` 구현 — `holidays.json`·`failures.json` 로드/기록 (파일 없으면 빈 상태로 시작)
- [x] 저장 직전 반올림 규칙 적용 (KRX 원화 가격·거래대금·시총 정수, 등락률 2자리 — 루트 CLAUDE.md)

---

### Phase 3 — 백필 루프와 CLI(그린 유지)

**작업 내용**:

- [x] `backfill.py` 구현 — 일자별 순차 수집, 조회 함수는 **주입**받아 pykrx 의존을 src에서 배제한다
  - 지수 백오프 재시도 (네트워크·세션 만료 대응, 스펙 §9)
  - 요청 간 지연
  - 빈 결과 → `holidays.json` 기록, 실패 → `failures.json` 기록 후 다음 일자 진행
  - 일자별 성공/실패·건수·소요시간 로깅
- [x] `names.py` 구현 — 최신 일자 기준 티커→종목명 매핑을 `names.csv`에 병합
- [x] `scripts/data/collect_snapshots.py` 작성 — `.env` 자격증명 확인, pykrx 조회 함수 주입, `TableLogger` 요약, `meta_manager` 기록, 종료 코드
- [x] `docs/COMMANDS.md`·`scripts/CLAUDE.md` 갱신

---

### Phase 4 — 시범 수집 검증(사용자 실행)

**작업 내용**:

- [x] 2019년 1월 시범 수집을 사용자에게 요청 (AI는 실행하지 않음 — 루트 CLAUDE.md 스크립트 실행 규칙)
- [x] 결과 확인: 영업일 커버리지(신정 등 휴장 판정), 파일 수, 컬럼·dtype, 티커 선행 0 보존, 건수
- [x] 재실행 시 기존 파일을 다시 쓰지 않고 건너뛰는지 확인 (불변 계약)

**Validation**:

- [x] 시범 수집 결과 로그 확보 및 스키마 확인

---

### 마지막 Phase — 문서 정리 및 최종 검증

**작업 내용**

- [x] 필요한 문서 업데이트 (README.md: 변경 없음 / `docs/COMMANDS.md`: 변경 있음)
- [x] `poetry run black .` 실행(자동 포맷 적용)
- [x] 변경 기능 및 전체 플로우 최종 검증
- [x] DoD 체크리스트 최종 업데이트 및 체크 완료
- [x] 전체 Phase 체크리스트 최종 업데이트 및 상태 확정

**Validation**:

- [x] `poetry run python validate_project.py` (passed=99, failed=0, skipped=0)

#### Commit Messages (Final candidates) — 5개 중 1개 선택

1. 수집 / 1단 전종목 스냅샷 수집기 구현 (스키마 검증·백필 루프·시각 게이트)
2. 수집 / 일별 스냅샷 백필 구현 및 저장 스키마 계약 테스트 고정
3. 수집 / 스냅샷 수집기 + 휴장·실패 체크포인트 처리 추가
4. 수집 / 당일 미확정 데이터 차단 시각 게이트 및 불변 파일 계약 구현
5. 수집 / 1단 수집 파이프라인 구현 + 실행 명령어·규칙 문서 갱신

## 7) 리스크(Risks)

- **KRX 세션 1시간 만료**: 백필이 2시간 이상이라 도중 재로그인이 발생한다. pykrx가 자동 재로그인하지만 실패 가능 → 지수 백오프 재시도로 흡수하고, 연속 실패 시 중단해 `failures.json`에 남긴다.
- **레이트리밋·차단**: 순차 수집 + 요청 간 지연을 유지한다. 병렬화하지 않는다.
- **두 API 종가 불일치**: 조회 시점이 달라 값이 어긋날 수 있다. 검증 실패 시 보간하지 않고 해당 일자를 실패 처리해 재수집 대상으로 남긴다.
- **불변 파일 계약 위반**: 재실행이 과거 파일을 덮어쓰면 git 히스토리가 오염된다. 저장 함수에서 기존 파일 존재 시 예외를 발생시켜 구조적으로 차단한다.
- **당일 시각 게이트의 기준값**: 확정 시각은 실측으로 확정되지 않았다(스펙 §0). 보수적으로 장 마감 한참 뒤로 잡고 상수로 분리해 조정 가능하게 둔다. 정확한 확정 시각은 ROADMAP Phase 4에서 다룬다.
- **휴장 오판**: 일시적 조회 실패를 휴장으로 기록하면 그 일자가 영구히 누락된다. 빈 결과는 **재시도를 모두 소진한 뒤에만** 휴장으로 기록한다.
- **시범 수집 범위**: 2019년 1월은 표본이 작아 드문 이상치(권리락·거래정지)를 다 못 볼 수 있다. 전체 백필 후 ROADMAP Phase 3 품질 리포트로 재확인한다.

## 8) 메모(Notes)

- **설계 판단(의존성 주입)**: 백필 루프를 스크립트에 두면 CLI 계층이 두꺼워지고(scripts/CLAUDE.md 위반), src에 두면 pykrx 의존이 생겨 테스트에서 네트워크가 필요해진다(tests/CLAUDE.md 위반). 조회 함수를 인자로 주입받는 방식으로 양쪽을 만족시킨다 — 루프·재시도·저장은 src, pykrx 함수 전달은 스크립트가 담당한다.
- **사용자 결정 사항**: (1) 당일은 시각 게이트 통과 후 수집한다. 근거 — 전략이 "오늘 종가로 신호 → 내일 예약매매"라 당일 데이터가 당일 밤에 필요하다. (2) `names.csv`는 최신 일자 1회만 수집한다. PIT 종목명은 나중에 소급해도 비용이 동일하므로 지금 미뤄도 손해가 없다.
- **PIT 종목명 관련 발견**: `get_market_sector_classifications(date, market)`가 종목코드→종목명을 시장당 1회 호출로 반환하며 `date` 인자를 받는다. 즉 스펙 §10.4가 전제한 "과거 시점 종목명 확보 불가"는 실제로는 우회 가능하다. 이번 범위에서는 쓰지 않되, 필요해지면 이 함수를 근거로 별도 plan을 만든다.
- 스킵은 발생시키지 않는 것을 원칙으로 한다. 불가피하게 발생하면 사유/해제 조건/후속 plan을 이 절에 기록한다.

### 진행 로그 (KST)

- 2026-07-27 10:16: plan 작성. 선행 plan(PLAN_pykrx_gate) Done 이후 착수.
- 2026-07-27 11:05: **Phase 4 검증 통과, plan 완료(Done).** 재실행 결과 2019-01-01이 휴장으로 판정돼 `holidays.json`에 기록되고 스냅샷 파일은 생성되지 않았다. 로컬 검증: 휴장 기록이 재조회를 막고(대상 목록에서 2019-01-01 제외), 저장 파일 20개의 컬럼 순서·dtype(int64/float64/object)·티커 선행 0 보존이 스펙 §7.2와 일치하며, 시총 검산(종가 × 상장주식수 = 시가총액)이 100% 일치했다. 실패 일자 0건. 체크포인트 동작도 실측으로 확인됐다 — 누락 일자가 1,974일에서 1,954일로 줄어 기수집 20일이 정확히 제외됐다. 최종 검증 passed=99 / failed=0 / skipped=0.
- 2026-07-27 11:00: 사용자 요청 — 자격증명 출처를 `.env` 하나로 고정. `collect/krx_credentials.py` 신설(`load_dotenv(override=True)`로 셸 환경 변수보다 `.env` 우선, 파일이 없으면 셸로 대체하지 않고 ValueError). 두 스크립트 모두 **pykrx import 이전**에 호출하도록 옮겼다 — pykrx가 import 시점에 `os.getenv`를 기본 인자로 평가해 세션을 만들기 때문이며, 이로써 실행 초반에 뜨던 오해 소지의 `KRX 로그인 실패` 메시지도 사라진다. 테스트 4건 추가.
- 2026-07-27 10:50: **시범 수집(1차)에서 결함 발견 및 수정.** 2019-01-01(신정)이 휴장으로 판정되지 않고 2,227종목짜리 스냅샷으로 저장됐다. 원인 — pykrx는 휴장일에 빈 DataFrame이 아니라 **전 종목 가격·거래량·시총이 0인 행**을 반환하며(`alternative=False`에서도), 내부 `holiday` 판정은 `alternative=True`일 때만 분기에 쓰인다. 스펙 §6의 "빈 DataFrame 반환" 서술이 실제와 달랐다. 대응 — (1) `snapshot.is_market_closed()` 추가: 시가·고가·저가·**종가까지** 전부 0이면 휴장으로 판정(개별 거래정지 종목은 종가가 남으므로 구분됨), (2) `backfill._collect_markets`가 빈 결과 대신 이 함수로 판정, (3) 회귀 테스트 5건 추가, (4) 오염된 `20190101.parquet` 삭제, (5) 스펙 §6 실측 기반 정정. 나머지 20개 파일은 전수 검사 결과 정상.
- 2026-07-27 10:40: Phase 0~3 완료. 중간 검증 결과 passed=90 / failed=0 / skipped=0 (Ruff·PyRight 통과). 계획 대비 조정 2건. (1) 시각 게이트 테스트는 freezegun 대신 **시각을 인자로 주입**해 고정했다 — `resolve_last_collectable_date(now)`가 현재 시각을 인자로 받으므로 전역 시간 패치가 불필요하고 더 결정적이다. (2) `collect_snapshots.py`에 `--limit` 인자를 추가했다 — 전체 백필이 두 시간 이상이라 실행 단위를 나눠야 하고 시범 수집 범위를 좁혀야 하기 때문이며, 사유는 scripts/CLAUDE.md에 기록했다.
- 2026-07-27 10:20: 사용자 결정 — 당일 확정 판정 시각을 **KST 17:00**으로 확정. 참고: KRX 시간외 단일가(16:00~18:00) 체결분이 일별 거래량·거래대금에 반영되므로 17시 값은 거래대금이 덜 찬 상태일 수 있다. 상수로 분리해 조정 가능하게 두며, 정확한 확정 시각은 ROADMAP Phase 4에서 실측한다.
