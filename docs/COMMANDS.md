# 실행 명령어 (단일 관리)

> 모든 실행 명령어는 이 문서에서 단일 관리한다. README.md와 CLAUDE.md에는 명령어를 기재하지 않는다.

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

> 스크립트는 사용자만 직접 실행한다 (CLAUDE.md 스크립트 실행 규칙).
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
