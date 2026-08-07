# 실행 명령어 (단일 관리)

> 모든 실행 명령어는 이 문서에서 단일 관리한다. CLAUDE.md에는 명령어를 기재하지 않는다.

## 환경 설정

### 최초 1회 (새 PC 부트스트랩)

Python 버전은 pyenv로 프로젝트 단위 고정하고, 패키지는 프로젝트 내부 `.venv/`에 격리한다.
Poetry 자체는 프로젝트 밖에 독립 설치해 프로젝트 가상환경을 오염시키지 않는다.

```bash
# 1. pyenv 설치 후 쉘 초기화 (zsh 기준, 이미 설정돼 있으면 생략)
brew install pyenv
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.zshrc
echo '[[ -d $PYENV_ROOT/bin ]] && export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.zshrc
echo 'eval "$(pyenv init - zsh)"' >> ~/.zshrc
exec zsh

# 2. Python 3.12 설치 + 프로젝트 로컬 고정 (.python-version 생성)
pyenv install 3.12
pyenv local $(pyenv latest 3.12)

# 3. Poetry 설치 (프로젝트 밖 독립 환경)
brew install poetry

# 4. 가상환경을 프로젝트 내부 .venv/ 로 고정 (poetry.toml 생성, install 전에 실행)
poetry config virtualenvs.in-project true --local

# 5. 프로젝트 가상환경 인터프리터를 pyenv의 3.12로 지정
poetry env use $(pyenv which python)
```

`.python-version`·`poetry.toml`·`poetry.lock`은 git에 포함해 다른 PC에서도 같은 버전이 재현되게 한다.

### 의존성 설치 (매번)

```bash
poetry install
```

### 환경 확인

```bash
poetry env info          # 사용 중인 인터프리터 경로/버전 확인
poetry run python -V     # 3.12.x 인지 확인
```

## 품질 검증

```bash
poetry run python validate_project.py               # Ruff + PyRight + Pytest 전체
poetry run python validate_project.py --only-lint   # Ruff만
poetry run python validate_project.py --only-pyright
poetry run python validate_project.py --only-tests
poetry run python validate_project.py --cov         # Pytest + 커버리지
```

## 포맷팅

```bash
poetry run black .
```

## 테스트 (특정 모듈/파일)

```bash
poetry run pytest tests/test_<모듈명>.py -v
```

## 데이터 수집

> 스크립트는 사용자가 직접 실행한다 (CLAUDE.md 스크립트 실행 규칙).
> 수집 스크립트가 구현되면 이 섹션에 명령어를 추가한다.

### KRX 로그인 설정 (수집 스크립트 실행 전 필수)

pykrx는 모든 KRX 조회를 인증 세션으로 보낸다. 자격증명이 없으면 응답 본문이 비어 조회가 실패한다.
[data.krx.co.kr](https://data.krx.co.kr) 계정을 만든 뒤 프로젝트 루트에 `.env`를 만들고 아래 두 항목을 채운다.

```
KRX_ID=<KRX 데이터포털 아이디>
KRX_PW=<비밀번호>
```

> `.env`는 `.gitignore`에 등록돼 있어 커밋되지 않는다. 자격증명을 저장소에 포함하지 않는다.
> 자격증명의 출처는 `.env` 하나로 고정한다 — 셸에 같은 이름의 환경 변수가 있어도 `.env` 값이 우선하며,
> `.env`가 없으면 셸 환경 변수로 대체하지 않고 즉시 중단한다.

### 스팟체크

```bash
# pykrx 검증 게이트 스팟체크 (스펙 §3.3, KRX 실제 호출 약 10회)
poetry run python scripts/data/check_pykrx_gates.py
```

### 1단 스냅샷 수집

누락된 영업일만 수집하며, 중단해도 재실행하면 이어서 진행된다 (파일 존재 = 체크포인트).

```bash
# 시범 수집 — 앞에서부터 21일치만 (2019년 1월 검증용)
poetry run python scripts/data/collect_snapshots.py --limit 21

# 전체 백필 — 누락 일자 전체 (약 6,800회 호출, 두 시간 이상 소요)
poetry run python scripts/data/collect_snapshots.py
```

> 당일 데이터는 KST 확정 시각(`SNAPSHOT_CONFIRM_HOUR_KST`) 이후에만 수집한다.
> 그 이전에 실행하면 전 영업일까지만 대상이 된다 — 장중 미확정 값이 저장되는 것을 막기 위함이다.

로그를 남기며 실행하려면 (3시간 이상 걸리므로 절전 방지를 함께 건다):

```bash
mkdir -p logs
caffeinate -i poetry run python scripts/data/collect_snapshots.py 2>&1 | tee logs/backfill_$(date +%Y%m%d_%H%M).log
```

> 진행 상황은 로그보다 파일 수가 정확하다 (파이프 출력은 버퍼링된다): `find storage/snapshots -name '*.parquet' | wc -l`

### 품질 검증 리포트

저장된 parquet만 읽는다. KRX 요청도, 데이터 수정도 하지 않는다.

```bash
poetry run python scripts/data/check_snapshot_quality.py
```

> 오류가 1건이라도 있으면 종료 코드 1을 반환한다. 상세 이슈 목록은 `storage/cache/`에 CSV로 저장된다.
> 외부 시세와 대조하는 절차는 아래 "데이터 진단·수기 검증"을 참고한다.

### 2단 수정주가 수집

1단 스냅샷 합집합의 전종목을 대상으로 하며, 미수집 종목만 수집한다 (파일 존재 = 체크포인트).
조회 구간은 수집 시작일 ~ 1단 최종 수집 일자로 자동 결정된다.

```bash
# 시범 수집 — 앞에서부터 20종목만
poetry run python scripts/data/collect_adjusted.py --limit 20

# 전량 수집 — 미수집 종목 전체 (약 3,100회 호출, 한 시간 이상 소요)
poetry run python scripts/data/collect_adjusted.py
```

로그를 남기며 실행하려면 (절전 방지를 함께 건다):

```bash
mkdir -p logs
caffeinate -i poetry run python scripts/data/collect_adjusted.py 2>&1 | tee logs/adjusted_$(date +%Y%m%d_%H%M).log
```

> 진행 상황은 파일 수로 확인한다: `ls storage/adjusted | wc -l`

`storage/adjusted/`는 **git 동기화 대상이 아니다**. 분할·증자가 발생하면 과거 전체가 재계산돼
파일이 통째로 다시 쓰이므로 히스토리에 담지 않는다. 다른 PC에서는 1단 스냅샷을 받은 뒤
위 전량 수집을 한 번 실행하면 같은 상태가 재현된다.

> **1단을 증분 수집했다면 2단도 이어서 실행한다.** 2단 조회 종료일은 1단 최종 수집 일자에 맞춰지므로,
> 1단만 최신이고 2단이 과거에 멈춰 있으면 그 사이에 액면분할·병합·배당락이 발생한 종목에서
> 최신일 종가 대조가 어긋난다(스펙 §0 실측). 1단 증분으로 신규 상장 종목이 늘어난 경우에도
> 2단 재실행이 미수집 종목만 자동으로 받는다.

재수집이 필요하면 해당 종목 파일을 지운 뒤 다시 실행한다 (1단과 같은 조작 모델).

```bash
rm storage/adjusted/005930.parquet
poetry run python scripts/data/collect_adjusted.py
```

### 2단 정합성 검증

저장된 1·2단 parquet만 읽는다. KRX 요청도, 데이터 수정도 하지 않는다.

```bash
poetry run python scripts/data/check_adjusted_quality.py
```

> 최신 일자의 수정 종가가 1단 원본 종가와 일치하는지, 상장주식수 급변일에 가짜 갭이 없는지 검사한다.
> 오류가 1건이라도 있으면 종료 코드 1을 반환하며, 상세 이슈 목록은 `storage/cache/`에 CSV로 저장된다.

### 백테스트 통합 패널 빌드

1·2단을 하나의 (일자, 티커) 패널로 합쳐 `storage/cache/panel/{YYYY}.parquet`에 저장한다.
KRX 요청 없이 저장된 parquet만 읽으며, 실행할 때마다 전량을 다시 쓴다 (파생 캐시).

```bash
poetry run python scripts/data/build_panel.py
```

> **선행 조건: 2단이 1단의 마지막 거래일까지 덮고 있어야 한다.** 2단이 뒤처져 있으면
> "2단 수정주가가 비는 (티커, 일자) 조합이 N건 있습니다"로 즉시 중단된다. 조용히 잘라내면
> 백테스트가 최신 구간을 말없이 빼놓고 돌기 때문이다.
>
> 2단 수집기는 **미수집 종목만** 받으므로(파일 존재 = 체크포인트) 그냥 다시 실행해도
> 기존 파일은 갱신되지 않는다. 1단을 증분 수집한 뒤에는 기존 파일을 지우고 전량을 다시 받아야 한다.

```bash
rm -rf storage/adjusted
poetry run python scripts/data/collect_adjusted.py   # 약 3,100회 호출, 한 시간 이상
poetry run python scripts/data/build_panel.py
```

빌드 결과는 행 수·종목 수·구간과 처리 규칙 플래그 건수를 표로 출력하며,
실행 이력은 `storage/meta/meta.json`의 `panel_build`에 쌓인다.
캐시가 원천 데이터와 어긋나면 로더가 예외를 던진다 — 자동 재생성하지 않으므로 위 명령으로 다시 빌드한다.

## 백테스트

통합 패널 위에서 전략을 실행한다. KRX 요청 없이 저장된 parquet만 읽으며,
결과는 `storage/backtest/{실행시각}_{라벨}/`에 `trades.csv`·`equity.csv`·`summary.json`으로 남는다.

```bash
# 전 기간
poetry run python scripts/backtest/run_backtest.py

# 구간 지정 (in-sample / out-of-sample 분할)
poetry run python scripts/backtest/run_backtest.py --start 2019-01-01 --end 2022-12-31 --label in-sample
poetry run python scripts/backtest/run_backtest.py --start 2023-01-01 --end 2026-07-27 --label out-of-sample

# 무비용 대조군 (새니티 체크 — 수수료·거래세·슬리피지를 모두 0으로)
poetry run python scripts/backtest/run_backtest.py --start 2019-01-01 --end 2022-12-31 --no-cost --label nocost

# 진입 방식 비교 (band=이탈 밴드 분할 / reclaim=회복 확인 / close-discount=종가 대비 할인)
poetry run python scripts/backtest/run_backtest.py --start 2019-01-01 --end 2022-12-31 --entry band --label band
poetry run python scripts/backtest/run_backtest.py --start 2019-01-01 --end 2022-12-31 --entry reclaim --label reclaim

# 손절 방식 비교 (band-floor=밴드 하한선 / fixed=평균단가 대비 고정 비율 / moving-average / swing-low)
poetry run python scripts/backtest/run_backtest.py --start 2019-01-01 --end 2022-12-31 --entry reclaim --stop fixed --label reclaim-fixed
```

> `--entry`·`--stop`을 생략하면 `src/krx_sprint/backtest/params.py`의 기본값을 쓴다.
> 두 축을 따로 고를 수 있게 둔 이유는 **진입 방식마다 알맞은 손절선이 다르기 때문**이다 —
> 회복 확인 진입은 밴드 하한선보다 위에서 사므로 같은 손절선을 쓰면 손절폭이 구조적으로 넓어진다.

> 패널 빌드가 선행 조건이다. 캐시가 원천 데이터와 어긋나면 로더가 예외를 던진다.
> 매매 종료일은 캘린더 끝에서 **2거래일 앞으로 자동 조정**된다 — 매도 세율이 결제일(T+2) 기준이라
> 마지막 이틀은 세율을 확정할 수 없기 때문이다.
>
> 전 기간(약 7.5년) 실행은 4~6분 걸린다. 요약표는 **비용 전/후 손익을 나란히** 출력하고,
> 미체결률·상한가로 놓친 주문·손절익절 동시 터치 같은 진단 항목을 함께 낸다.

`storage/backtest/`는 git 동기화 대상이 아니다 (실행마다 쌓이며 언제든 재생성 가능).

## 신호 예측력 이벤트 스터디

**매매 규칙 없이** 신호가 미래 수익률을 예측하는지만 측정한다. 진입가·손절·익절·자금배분·비용이
들어가지 않으므로 성과가 나빠도 원인이 신호 하나로 좁혀진다. 백테스트는 체결된 트레이드만 남아
표본이 수십~수백 건이지만, 이벤트 스터디는 **체결 여부와 무관하게 모든 신호**를 세므로
표본이 수만 건이 된다.

```bash
# 전 기간
poetry run python scripts/backtest/run_event_study.py --label full-period

# 구간 지정 (시장 국면별 분해)
poetry run python scripts/backtest/run_event_study.py --start 2019-01-01 --end 2022-12-31 --label in-sample
```

> 결과는 `storage/backtest/{실행시각}_{라벨}/`에 `layer_summary.csv`·`summary.json`으로 남는다.
> 신호 계층(유니버스 → 급등 → 테마 소속 → 대장주 → 기준봉 → 하락차수 → 정배열)을 쌓으며
> **각 층에서 초과수익이 어떻게 변하는지**를 낸다. 어느 요소가 알파를 더하고 깎는지가 이 표에서 보인다.
>
> **검정은 일자 단위 표본이 기본이다.** 같은 날 같은 테마의 종목은 수익률이 강하게 상관돼 있어
> 독립이 아니며, 종목 단위로 t검정을 하면 표준오차가 과소추정되어 없는 유의성이 생긴다.
> 종목 단위 평균은 참고로만 병기한다.
>
> 계층 7개 × 보유구간 5개 × 기준 2개라 검정이 70개다. **개별 칸의 t값으로 결론을 내지 않는다** —
> 층을 쌓을 때의 변화 방향이 판단 근거다.
>
> 전 기간 실행은 백테스트와 비슷하게 수 분이 걸린다 (매일 클러스터링을 다시 하기 때문).

## 데이터 진단·수기 검증

자동 리포트는 **내부 일관성**만 본다. 값이 실제 시장과 맞는지는 외부 대조가 필요하며,
확인된 값은 앵커로 등록해 다음부터 자동 검증되게 한다. 검증 규칙과 지금까지의 결과는
[데이터수집_스펙_v2.md](데이터수집_스펙_v2.md) §3.1·§8을 참고한다.

### 외부 시세 대조

네이버 금융(`finance.naver.com`, `api.finance.naver.com`)은 접근이 차단되므로 Yahoo Finance를 쓴다.
코스피는 `.KS`, 코스닥은 `.KQ` 접미사를 붙인다.

```
https://query1.finance.yahoo.com/v8/finance/chart/005930.KS?period1=<시작 epoch>&period2=<종료 epoch>&interval=1d
```

> **대조 대상 주의**: Yahoo가 주는 값은 수정주가 계열이므로 **2단**과 비교한다.
> 액면분할·병합은 KRX와 일치하지만 **배당·증자 조정 계수는 산식이 달라 어긋난다**(스펙 §3.1 실측).
> 1단 원본가를 대조하려면 액션이 없었던 종목·구간을 고른다.

### 특정 종목 시세 확인

```bash
poetry run python -c "
from datetime import date
from krx_sprint.collect.snapshot_store import list_collected_dates, load_snapshot
TICKER, START, END = '005930', date(2026,7,21), date(2026,7,27)   # ← 교체
for d in sorted(x for x in list_collected_dates() if START <= x <= END):
    r = load_snapshot(d)
    r = r[r['ticker']==TICKER]
    if r.empty: print(d, '없음'); continue
    s = r.iloc[0]
    print(f\"{d} 시{s['open']:>8,} 고{s['high']:>8,} 저{s['low']:>8,} 종{s['close']:>8,} 거래량{s['volume']:>12,} 등락{s['change_rate']:>7.2f}%\")
"
```

### 특정일 거래대금·시총 상위

```bash
poetry run python -c "
from datetime import date
import pandas as pd
from krx_sprint.collect.snapshot_store import load_snapshot
TARGET, COLUMN = date(2021, 1, 11), 'value'   # ← 'market_cap' 으로 바꾸면 시총 상위
names = pd.read_csv('storage/names.csv', dtype=str).set_index('ticker')['name'].to_dict()
df = load_snapshot(TARGET)
top = df.nlargest(20, COLUMN)[['ticker','market','close','change_rate',COLUMN]].copy()
top.insert(1, 'name', top['ticker'].map(names))
top['억'] = (top[COLUMN]/1e8).round(0).astype(int)
print(f'{TARGET} {COLUMN} 상위 20 (전체 {len(df)}종목)')
print(top[['ticker','name','market','close','change_rate','억']].to_string(index=False))
"
```

> 폐지 종목의 이름이 비는 것은 정상이다 — `names.csv`는 최신 상장분 기준이다.
> 대조 구간은 극단값이 있는 날이 판별력이 높다: 2020-03-13(서킷브레이커), 2020-03-19(코로나 저점),
> 2021-01-11(코스피 최고가 부근), 2022-09-30(하락장 저점 부근).

### 확인한 값을 앵커로 등록

1. `src/krx_sprint/collect/quality.py`의 `ANCHORS`에 `AnchorRecord` 추가
2. [데이터수집_스펙_v2.md](데이터수집_스펙_v2.md) §3.1에 확인 경위 기록
3. 품질 리포트 재실행으로 통과 확인

### 이상을 발견했을 때

**데이터를 고치지 않는다.** 보간·수정은 금지다(루트 CLAUDE.md). 해당 일자를 재수집한다.

```bash
rm storage/snapshots/2021/20210111.parquet    # 1단은 불변 계약이라 덮어쓰기가 안 된다
poetry run python scripts/data/collect_snapshots.py
poetry run python scripts/data/check_snapshot_quality.py
```

재수집해도 같은 값이면 KRX 원본이 그러한 것이므로, **처리 방침을 스펙 §8에 기록**하고 백테스트로 넘긴다.
