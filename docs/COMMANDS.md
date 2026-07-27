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
