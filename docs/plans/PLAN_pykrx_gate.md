# Implementation Plan: pykrx 검증 게이트 실측 (ROADMAP Phase 1)

> 작성/운영 규칙(SoT): 반드시 [docs/CLAUDE.md](../CLAUDE.md)를 참고하세요.  
> (이 템플릿을 수정하거나 새로운 양식의 계획서를 만들 때도 [docs/CLAUDE.md](../CLAUDE.md)를 포인터로 두고 준수합니다.)

**상태**: 🟡 Draft

---

🚫 **이 영역은 삭제/수정 금지** 🚫

**상태 옵션**: 🟡 Draft / 🔄 In Progress / ✅ Done

**Done 처리 규칙**:

- ✅ Done 조건: DoD 모두 [x] + `skipped=0` + `failed=0`
- ⚠️ **스킵이 1개라도 존재하면 Done 처리 금지 + DoD 테스트 항목 체크 금지**
- 상세: [docs/CLAUDE.md](../CLAUDE.md) 섹션 3, 5 참고

---

**작성일**: 2026-07-27 09:18
**마지막 업데이트**: 2026-07-27 09:18
**관련 범위**: collect(신규), scripts/data, tests, docs
**관련 문서**: [scripts/CLAUDE.md](../../scripts/CLAUDE.md), [tests/CLAUDE.md](../../tests/CLAUDE.md), [docs/CLAUDE.md](../CLAUDE.md), [docs/데이터수집_스펙_v2.md](../데이터수집_스펙_v2.md), [docs/ROADMAP.md](../ROADMAP.md)

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

- [ ] 목표 1: 스펙 §3.3 게이트 1(1단 스냅샷의 폐지 종목 보존)·게이트 2(2단 개별 조회의 폐지 종목 지원)·게이트 3(`market="ALL"`의 코넥스 포함 여부)를 **실측으로 확정**한다
- [ ] 목표 2: 게이트 **판정 규칙**을 pykrx 없이 검증 가능한 순수 함수로 분리하고 스텁 기반 테스트로 계약을 고정한다
- [ ] 목표 3: 부가 실측(pykrx 설치 버전·KRX 로그인 경고 여부·거래정지일 OHL=0 동작·당일 데이터 확정 시각 관측)을 스팟체크 스크립트 1회 실행으로 수집한다
- [ ] 목표 4: 실측 결과를 스펙 §0 "구현 시 실측 검증할 것" 항목에 반영해, Phase 2(수집기) 착수 조건을 닫는다

## 2) 비목표(Non-Goals)

- 1단 스냅샷 수집기 본체 구현 (ROADMAP Phase 2) — 게이트 결과가 설계를 바꿀 수 있으므로 이 plan에서 다루지 않는다
- 품질 검증 규칙의 코드화 (거래정지 패턴 분류 함수 등, ROADMAP Phase 3) — 이 plan은 해당 동작을 **관측**만 하고 판정 함수는 만들지 않는다
- parquet 저장·`storage/` 데이터 생성 — 스팟체크는 데이터 파일을 만들지 않는다
- 게이트 실패 시의 대응 서브루틴(상장폐지 종목 목록 별도 수집 등) 구현 — 실패가 확인되면 별도 plan으로 분리한다
- "당일 데이터 확정 시각"의 자동 확정 — 1회 실행으로 판정 불가하므로 관측값 출력까지만 담당한다 (§7 Risks 참고)

## 3) 배경/맥락(Context)

### 현재 문제점 / 동기

- 스펙 v2는 pykrx 동작에 대한 3개 가정 위에 서 있다. 이 가정이 틀리면 **생존편향이 조용히 되살아나고**(게이트 1·2), **유니버스에 코넥스가 섞인다**(게이트 3). 두 경우 모두 백테스트 성과가 왜곡되며, 수집을 마친 뒤에는 되돌리는 비용이 크다.
- pykrx는 KRX 웹을 래핑하므로 동작이 조용히 바뀔 수 있다. 기억이나 문서가 아니라 **설치된 버전에서의 실측**으로 확정해야 한다.
- ROADMAP 원칙: "Phase 1(실측)이 끝나기 전에 수집기 본체를 만들지 않는다."

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

- [ ] 게이트 1·2·3의 실측 결과가 확보되고 스펙 §0에 기록됨
- [ ] 게이트 판정 순수 함수에 대한 스텁 기반 테스트 추가 (네트워크 호출 없음)
- [ ] `poetry run python validate_project.py` 통과 (failed=0, skipped=0; passed/failed/skipped 수 기록)
- [ ] `poetry run black .` 실행 완료 (마지막 Phase에서 자동 포맷 적용)
- [ ] 필요한 문서 업데이트(README.md / `docs/COMMANDS.md` / CLAUDE.md / plan 등 — 각각 변경 여부 명시)
- [ ] plan 체크박스 최신화(Phase/DoD/Validation 모두 반영)

## 5) 변경 범위(Scope)

### 변경 대상 파일(예상)

- `src/krx_sprint/collect/__init__.py` (신규)
- `src/krx_sprint/collect/gate_checks.py` (신규) — 게이트 판정 순수 함수
- `tests/test_gate_checks.py` (신규)
- `scripts/data/check_pykrx_gates.py` (신규) — pykrx 호출·출력·메타 기록
- `docs/데이터수집_스펙_v2.md` (수정) — §0 실측 항목에 결과 기록
- `scripts/CLAUDE.md` (수정) — 메타데이터 "지원 타입"에 게이트 스팟체크 타입 추가 (해당 문서 §5가 요구)
- `README.md`: **변경 없음** (현재 저장소에 README.md 파일이 존재하지 않으며, 이 plan에서 생성하지 않는다)
- `docs/COMMANDS.md`: **변경 있음** — "데이터 수집" 섹션에 스팟체크 스크립트 실행 명령어 추가

### 데이터/결과 영향

- `storage/` 데이터 파일 생성 없음 (스냅샷·수정주가 미수집)
- `storage/meta/meta.json`에 스팟체크 실행 이력 1건 추가 (meta_manager 순환 저장)
- 게이트 결과에 따라 스펙 §5(유니버스 호출 방식)·§7(저장 설계)의 미세 조정이 발생할 수 있음

## 6) 단계별 계획(Phases)

### Phase 0 — 게이트 판정 규칙을 테스트로 먼저 고정(레드)

> 해당 사유: 게이트 판정은 "코넥스가 유니버스에 섞이는가", "폐지 종목이 보존되는가"라는 **핵심 인바리언트**를 결정한다.

**작업 내용**:

- [ ] 판정 함수의 인터페이스·반환 타입·예외 정책을 확정한다
  - 폐지 종목 보존 판정: 스냅샷 DataFrame(index=티커)에 대상 티커가 존재하는지
  - 코넥스 포함 판정: `ALL` 티커 집합이 `KOSPI ∪ KOSDAQ`의 진부분집합이 아닌 경우(초과분 존재) 코넥스 포함으로 본다
  - 빈 DataFrame(휴장·조회 실패)은 "없음"으로 조용히 넘기지 않고 ValueError로 구분한다 (루트 CLAUDE.md 명시적 검증)
- [ ] `tests/test_gate_checks.py`에 스텁 DataFrame/집합 기반 테스트를 최대한 먼저 작성 (레드 허용)
  - 티커 존재/부재, 빈 입력, 티커 dtype이 문자열이 아닌 경우
  - ALL이 KOSPI+KOSDAQ와 동일한 경우 / 초과 티커가 있는 경우 / ALL이 더 적은 경우(비정상)

---

### Phase 1 — 판정 함수 구현(그린 유지)

**작업 내용**:

- [ ] `src/krx_sprint/collect/gate_checks.py` 구현으로 Phase 0 테스트를 통과시킨다
- [ ] 판정 결과는 boolean 단독이 아니라 근거 수치(건수·초과 티커 샘플)를 함께 담은 결과 타입으로 반환한다 (스펙 §0 기록용)
- [ ] 이 모듈은 pykrx를 import 하지 않는다 — 호출은 스크립트 계층이 담당한다 (테스트에서 네트워크 의존 제거)

---

### Phase 2 — 스팟체크 스크립트 작성(그린 유지, 실행은 사용자)

**작업 내용**:

- [ ] `scripts/data/check_pykrx_gates.py` 작성
  - 게이트 1: 한진해운(`117930`)이 폐지 전 일자의 `get_market_ohlcv_by_ticker(date, market)` 결과에 존재하는가
  - 게이트 2: `get_market_ohlcv(fromdate, todate, "117930", adjusted=True/False)`가 데이터를 반환하는가
  - 게이트 3: `get_market_ticker_list(date, market="ALL")` vs `KOSPI + KOSDAQ` 티커 집합 비교
  - 부가 실측: 설치된 pykrx 버전, 실행 중 발생한 경고, 거래정지 의심 종목의 OHL/거래량 패턴 샘플, 실행 시각과 당일 조회 결과
- [ ] `@cli_exception_handler` 적용, 모듈 레벨 `logger`, `TableLogger`로 결과 요약 출력, 종료 코드 반환
- [ ] 요청 간 지연(sleep)을 넣고 총 호출 수를 최소로 유지 (스펙 §9 레이트리밋)
- [ ] `meta_manager.save_metadata`로 실행 이력 기록
- [ ] `docs/COMMANDS.md`에 실행 명령어 추가
- [ ] 스크립트 실행을 사용자에게 요청 (AI는 실행하지 않음 — 루트 CLAUDE.md 스크립트 실행 규칙)

**Validation**:

- [ ] 사용자 실행 결과 로그 확보 (게이트 3건 + 부가 실측 항목)

---

### Phase 3 — 실측 결과 반영

**작업 내용**:

- [ ] 스펙 §0 "구현 시 실측 검증할 것" 항목에 게이트별 결과(값·근거·측정 일자)를 기록
- [ ] 게이트 실패 항목이 있으면 대응 방침을 스펙에 기록하고, 구현이 필요하면 후속 plan으로 분리
- [ ] `scripts/CLAUDE.md`의 메타데이터 지원 타입 목록 갱신

---

### 마지막 Phase — 문서 정리 및 최종 검증

**작업 내용**

- [ ] 필요한 문서 업데이트 (README.md: 변경 없음 / `docs/COMMANDS.md`: 변경 있음)
- [ ] `poetry run black .` 실행(자동 포맷 적용)
- [ ] 변경 기능 및 전체 플로우 최종 검증
- [ ] DoD 체크리스트 최종 업데이트 및 체크 완료
- [ ] 전체 Phase 체크리스트 최종 업데이트 및 상태 확정

**Validation**:

- [ ] `poetry run python validate_project.py` (passed=\_\_, failed=\_\_, skipped=\_\_)

#### Commit Messages (Final candidates) — 5개 중 1개 선택

1. 검증 / pykrx 게이트 실측 스팟체크 추가 및 판정 규칙 테스트 고정
2. 검증 / 폐지종목 보존·코넥스 포함 여부 실측 게이트 구현
3. 수집 / 수집기 착수 전 pykrx 동작 실측 게이트 및 결과 스펙 반영
4. 검증 / 게이트 판정 순수 함수 분리 + 스텁 기반 테스트 추가
5. 문서 / pykrx 실측 결과 스펙 §0 반영 및 실행 명령어 정리

## 7) 리스크(Risks)

- **게이트 1·2 실패(폐지 종목 미조회)**: 생존편향 방지 설계의 전제가 깨진다. → 스펙 §3.3의 대응(상장폐지 종목 목록 별도 수집·1단 원본가에 수동 수정계수) 검토가 필요하고, Phase 2 착수가 지연된다. 실패해도 데이터를 보간하지 않는다.
- **티커 재사용(스펙 §10.4)**: `117930`이 다른 회사에 재할당됐다면 게이트 1·2 결과 해석이 왜곡될 수 있다. → 조회된 종목명·기간을 함께 출력해 육안 확인한다.
- **KRX 레이트리밋/차단**: 스팟체크 자체는 호출 수가 작지만 반복 실행 시 위험. → 요청 간 지연, 실패 시 재실행 간격 확보.
- **pykrx 최신 버전의 동작 변경/로그인 경고**: 함수는 있으나 빈 DataFrame을 반환할 수 있다. → 빈 결과를 "없음"으로 판정하지 않고 예외/별도 표시로 구분한다(Phase 0 정책).
- **당일 데이터 확정 시각**: 1회 실행으로 확정 불가. → 스크립트는 실행 시각과 당일 조회 결과만 기록하고, 서로 다른 시각의 2~3회 실행 결과로 사용자가 판단한다. 미확정 상태로 남으면 Phase 4(증분 업데이트)에서 다시 다룬다.
- **스펙 문서 수정**: `docs/데이터수집_스펙_v2.md`는 확정본이다. → §0 실측 항목 기록 외의 내용은 사용자 승인 없이 수정하지 않는다.

## 8) 메모(Notes)

- **설계 판단(계층 분리)**: 스팟체크 전체를 스크립트 1개로 만드는 단순안을 먼저 검토했으나 채택하지 않았다. 근거 — scripts/CLAUDE.md는 CLI 계층의 도메인 로직 포함을 금지하고, tests/CLAUDE.md는 외부 API 호출부를 스텁으로 대체하도록 요구한다. 판정 규칙만 `src`로 분리하면 pykrx 없이 테스트 가능해진다. 대신 `src` 모듈은 판정 함수로만 최소화하고 pykrx import를 두지 않는다.
- **Context7 확인 결과 (2026-07-27)**: `get_market_ohlcv_by_ticker(date, market="KOSPI", alternative=False)`, `get_market_ticker_list(date=None, market="KOSPI")`는 스펙 §6과 일치. `get_market_cap_by_ticker`는 공식 문서 기준 `(date, market="ALL", acending=False, alternative=False)`로 **기본값이 `ALL`**이며 스펙 §6의 표기(`market="KOSPI"`)와 다르다 → 코넥스 혼입을 막기 위해 호출 시 `market`을 항상 명시한다. `get_market_ohlcv`는 `adjusted` 기본값이 `True`다.
- 스킵은 발생시키지 않는 것을 원칙으로 한다. 불가피하게 발생하면 사유/해제 조건/후속 plan을 이 절에 기록한다.

### 진행 로그 (KST)

- 2026-07-27 09:18: plan 작성 (ROADMAP Phase 0 완료 대기 중 선작성). 실행 환경(Python 3.12·Poetry) 설치는 사용자 진행.
- 2026-07-27 09:30: 환경 설치 완료 후 설치 버전 확인 중 부가 실측 항목이 일부 관측됨. 설치 버전은 **pykrx 1.2.8**이며, `import pykrx` 시점에 `KRX 로그인 실패: KRX_ID 또는 KRX_PW 환경 변수가 설정되지 않았습니다.` 메시지가 출력된다. 이 메시지가 실제 조회를 차단하는지는 미확정 → Phase 2 스팟체크에서 조회 성공 여부와 함께 판정한다.
- 2026-07-27 09:30: 실행 환경의 Python 3.12.13이 `_lzma` 확장 없이 빌드됨(빌드 시점에 xz 미설치). parquet(pyarrow 자체 코덱)·평문 CSV 경로에는 영향이 없으나, 재빌드 여부는 사용자 결정 사항으로 남긴다.
