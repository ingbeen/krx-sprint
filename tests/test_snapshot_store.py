"""snapshot_store / meta_store 테스트

parquet 저장의 불변 파일 계약과 메타(휴장·실패) 기록 계약을 고정한다 (스펙 §7.1·§7.3).
"""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from krx_sprint.collect.meta_store import (
    load_failures,
    load_holidays,
    record_failure,
    record_holiday,
    remove_failure,
)
from krx_sprint.collect.snapshot_store import (
    list_all_tickers,
    list_collected_dates,
    list_first_seen_dates,
    load_snapshot,
    save_snapshot,
    snapshot_path,
)
from krx_sprint.common_constants import SNAPSHOT_COLUMNS

TARGET = date(2019, 1, 2)


def _snapshot(ticker: str = "005930") -> pd.DataFrame:
    """저장용 최소 스냅샷을 만든다."""
    return pd.DataFrame(
        [[ticker, "KOSPI", 1000, 1100, 900, 1050, 500, 525000, 1.5, 10500000, 10000]],
        columns=SNAPSHOT_COLUMNS,
    )


class TestSnapshotPath:
    """저장 경로 규칙을 고정한다 (스펙 §7.2)."""

    def test_uses_year_folder_and_date_filename(self):
        """
        목적: 경로가 {YYYY}/{YYYYMMDD}.parquet 규칙을 따른다.

        Given: 2019-01-02
        When: snapshot_path 호출
        Then: 2019/20190102.parquet 로 끝난다
        """
        # Given / When
        result = snapshot_path(TARGET, base_dir=Path("/tmp/snapshots"))

        # Then
        assert result == Path("/tmp/snapshots/2019/20190102.parquet")


class TestSaveSnapshot:
    """불변 파일 계약을 고정한다."""

    def test_creates_parquet_with_year_folder(self, tmp_path: Path):
        """
        목적: 연도 폴더를 만들고 parquet을 저장한다.

        Given: 비어 있는 저장 경로
        When: save_snapshot 호출
        Then: 파일이 생성되고 내용이 왕복 저장된다
        """
        # Given / When
        saved = save_snapshot(_snapshot(), TARGET, base_dir=tmp_path)

        # Then
        assert saved.exists()
        loaded = pd.read_parquet(saved)
        assert list(loaded.columns) == SNAPSHOT_COLUMNS
        assert loaded["ticker"].tolist() == ["005930"]

    def test_rejects_overwrite_of_existing_file(self, tmp_path: Path):
        """
        목적: 이미 저장된 과거 일자 파일을 덮어쓰지 않는다 (스펙 §7.1 불변 계약).

        Given: 이미 저장된 일자
        When: 같은 일자로 다시 save_snapshot 호출
        Then: FileExistsError가 발생한다
        """
        # Given
        save_snapshot(_snapshot(), TARGET, base_dir=tmp_path)

        # When / Then
        with pytest.raises(FileExistsError, match="20190102"):
            save_snapshot(_snapshot(), TARGET, base_dir=tmp_path)

    def test_rejects_wrong_schema(self, tmp_path: Path):
        """
        목적: 스키마가 어긋난 DataFrame을 저장하지 않는다.

        Given: 컬럼이 하나 빠진 스냅샷
        When: save_snapshot 호출
        Then: ValueError가 발생한다
        """
        # Given
        broken = _snapshot().drop(columns=["shares"])

        # When / Then
        with pytest.raises(ValueError, match="컬럼"):
            save_snapshot(broken, TARGET, base_dir=tmp_path)

    def test_rejects_empty_snapshot(self, tmp_path: Path):
        """
        목적: 빈 스냅샷을 파일로 남기지 않는다 (경계 조건).

        Given: 행이 없는 스냅샷
        When: save_snapshot 호출
        Then: ValueError가 발생한다
        """
        # Given
        empty = _snapshot().iloc[0:0]

        # When / Then
        with pytest.raises(ValueError, match="비어"):
            save_snapshot(empty, TARGET, base_dir=tmp_path)


class TestLoadSnapshot:
    """저장된 스냅샷 로드 계약을 고정한다."""

    def test_loads_saved_snapshot(self, tmp_path: Path):
        """
        목적: 저장한 스냅샷을 스키마 그대로 다시 읽는다.

        Given: 저장된 스냅샷
        When: load_snapshot 호출
        Then: 컬럼과 값이 유지된다
        """
        # Given
        save_snapshot(_snapshot(), TARGET, base_dir=tmp_path)

        # When
        loaded = load_snapshot(TARGET, base_dir=tmp_path)

        # Then
        assert list(loaded.columns) == SNAPSHOT_COLUMNS
        assert loaded["ticker"].tolist() == ["005930"]

    def test_rejects_missing_file(self, tmp_path: Path):
        """
        목적: 없는 일자를 조용히 빈 결과로 넘기지 않는다 (경계 조건).

        Given: 저장되지 않은 일자
        When: load_snapshot 호출
        Then: FileNotFoundError가 발생한다
        """
        # Given / When / Then
        with pytest.raises(FileNotFoundError, match="20190102"):
            load_snapshot(TARGET, base_dir=tmp_path)


class TestListCollectedDates:
    """수집 완료 체크포인트(파일 존재) 판정을 고정한다."""

    def test_returns_saved_dates(self, tmp_path: Path):
        """
        목적: 저장된 파일명에서 수집 완료 일자를 복원한다.

        Given: 두 일자가 저장된 상태
        When: list_collected_dates 호출
        Then: 두 일자가 모두 반환된다
        """
        # Given
        save_snapshot(_snapshot(), date(2019, 1, 2), base_dir=tmp_path)
        save_snapshot(_snapshot(), date(2020, 3, 4), base_dir=tmp_path)

        # When
        result = list_collected_dates(base_dir=tmp_path)

        # Then
        assert result == {date(2019, 1, 2), date(2020, 3, 4)}

    def test_returns_empty_for_missing_directory(self, tmp_path: Path):
        """
        목적: 저장 폴더가 없으면 빈 집합을 반환한다 (최초 실행, 경계 조건).

        Given: 존재하지 않는 폴더
        When: list_collected_dates 호출
        Then: 빈 집합이다
        """
        # Given / When
        result = list_collected_dates(base_dir=tmp_path / "없음")

        # Then
        assert result == set()

    def test_rejects_malformed_filename(self, tmp_path: Path):
        """
        목적: 규칙에 맞지 않는 파일을 조용히 건너뛰지 않는다.

        Given: 날짜 형식이 아닌 parquet 파일
        When: list_collected_dates 호출
        Then: ValueError가 발생한다
        """
        # Given
        year_dir = tmp_path / "2019"
        year_dir.mkdir(parents=True)
        (year_dir / "invalid.parquet").touch()

        # When / Then
        with pytest.raises(ValueError, match="파일명"):
            list_collected_dates(base_dir=tmp_path)


class TestListAllTickers:
    """생존편향 방지 유니버스(일별 스냅샷 합집합) 산출을 고정한다 (스펙 §3.2)."""

    def test_returns_union_of_all_dates(self, tmp_path: Path):
        """
        목적: 여러 일자에 흩어진 티커를 합집합으로 모은다.

        Given: 일자마다 다른 티커가 저장된 상태
        When: list_all_tickers 호출
        Then: 두 티커가 모두 반환된다
        """
        # Given
        save_snapshot(_snapshot("005930"), date(2019, 1, 2), base_dir=tmp_path)
        save_snapshot(_snapshot("000660"), date(2019, 1, 3), base_dir=tmp_path)

        # When
        result = list_all_tickers(base_dir=tmp_path)

        # Then
        assert result == {"005930", "000660"}

    def test_includes_delisted_ticker(self, tmp_path: Path):
        """
        목적: 이후 일자에서 사라진 종목도 유니버스에 남는다 (생존편향 방지의 핵심).

        Given: 첫날에만 등장하고 다음 날 사라진 티커
        When: list_all_tickers 호출
        Then: 사라진 티커가 여전히 포함된다
        """
        # Given
        save_snapshot(_snapshot("117930"), date(2019, 1, 2), base_dir=tmp_path)
        save_snapshot(_snapshot("005930"), date(2019, 1, 3), base_dir=tmp_path)

        # When
        result = list_all_tickers(base_dir=tmp_path)

        # Then
        assert "117930" in result

    def test_returns_empty_for_missing_directory(self, tmp_path: Path):
        """
        목적: 저장 폴더가 없으면 빈 집합을 반환한다 (최초 실행, 경계 조건).

        Given: 존재하지 않는 폴더
        When: list_all_tickers 호출
        Then: 빈 집합이다
        """
        # Given / When
        result = list_all_tickers(base_dir=tmp_path / "없음")

        # Then
        assert result == set()


class TestListFirstSeenDates:
    """티커별 1단 최초 등장일 산출을 고정한다 (2단 유니버스 절단의 기준)."""

    def test_returns_earliest_date_per_ticker(self, tmp_path: Path):
        """
        목적: 티커마다 가장 이른 등장 일자를 반환한다.

        Given: 한 티커가 두 일자에, 다른 티커가 뒤 일자에만 등장한 상태
        When: list_first_seen_dates 호출
        Then: 각 티커의 최초 등장일이 반환된다
        """
        # Given
        save_snapshot(_snapshot("005930"), date(2019, 1, 2), base_dir=tmp_path)
        second = pd.concat([_snapshot("005930"), _snapshot("000660")], ignore_index=True)
        save_snapshot(second, date(2019, 1, 3), base_dir=tmp_path)

        # When
        result = list_first_seen_dates(base_dir=tmp_path)

        # Then
        assert result == {"005930": date(2019, 1, 2), "000660": date(2019, 1, 3)}

    def test_includes_delisted_ticker(self, tmp_path: Path):
        """
        목적: 이후 사라진 종목도 최초 등장일이 남는다 (생존편향 방지, 경계 조건).

        Given: 첫날에만 등장하고 사라진 티커
        When: list_first_seen_dates 호출
        Then: 그 티커의 최초 등장일이 포함된다
        """
        # Given
        save_snapshot(_snapshot("117930"), date(2019, 1, 2), base_dir=tmp_path)
        save_snapshot(_snapshot("005930"), date(2019, 1, 3), base_dir=tmp_path)

        # When
        result = list_first_seen_dates(base_dir=tmp_path)

        # Then
        assert result["117930"] == date(2019, 1, 2)

    def test_returns_empty_for_missing_directory(self, tmp_path: Path):
        """
        목적: 저장 폴더가 없으면 빈 결과를 반환한다 (최초 실행, 경계 조건).

        Given: 존재하지 않는 폴더
        When: list_first_seen_dates 호출
        Then: 빈 dict이다
        """
        # Given / When
        result = list_first_seen_dates(base_dir=tmp_path / "없음")

        # Then
        assert result == {}


class TestMetaStore:
    """휴장·실패 기록 계약을 고정한다."""

    def test_holidays_roundtrip(self, tmp_path: Path):
        """
        목적: 휴장 일자를 기록하고 다시 읽을 수 있다.

        Given: 빈 상태
        When: 휴장 2건 기록 후 로드
        Then: 두 일자가 모두 조회된다
        """
        # Given
        path = tmp_path / "holidays.json"

        # When
        record_holiday(path, date(2019, 1, 1))
        record_holiday(path, date(2019, 3, 1))

        # Then
        assert load_holidays(path) == {date(2019, 1, 1), date(2019, 3, 1)}

    def test_holidays_are_idempotent(self, tmp_path: Path):
        """
        목적: 같은 일자를 두 번 기록해도 중복되지 않는다.

        Given: 이미 기록된 휴장 일자
        When: 같은 일자를 다시 기록
        Then: 집합 크기가 1이다
        """
        # Given
        path = tmp_path / "holidays.json"
        record_holiday(path, date(2019, 1, 1))

        # When
        record_holiday(path, date(2019, 1, 1))

        # Then
        assert load_holidays(path) == {date(2019, 1, 1)}

    def test_load_holidays_returns_empty_for_missing_file(self, tmp_path: Path):
        """
        목적: 파일이 없으면 빈 집합을 반환한다 (최초 실행, 경계 조건).

        Given: 존재하지 않는 파일
        When: load_holidays 호출
        Then: 빈 집합이다
        """
        # Given / When / Then
        assert load_holidays(tmp_path / "없음.json") == set()

    def test_failure_records_reason(self, tmp_path: Path):
        """
        목적: 실패 일자와 사유를 함께 남긴다 (재수집 대상 추적).

        Given: 빈 상태
        When: 실패 기록
        Then: 일자별 사유가 조회된다
        """
        # Given
        path = tmp_path / "failures.json"

        # When
        record_failure(path, date(2019, 1, 3), "조회 실패")

        # Then
        assert load_failures(path) == {date(2019, 1, 3): "조회 실패"}

    def test_failure_is_removed_after_success(self, tmp_path: Path):
        """
        목적: 재수집에 성공하면 실패 목록에서 제거한다.

        Given: 실패로 기록된 일자
        When: remove_failure 호출
        Then: 실패 목록이 비워진다
        """
        # Given
        path = tmp_path / "failures.json"
        record_failure(path, date(2019, 1, 3), "조회 실패")

        # When
        remove_failure(path, date(2019, 1, 3))

        # Then
        assert load_failures(path) == {}

    def test_remove_failure_is_safe_when_absent(self, tmp_path: Path):
        """
        목적: 기록에 없는 일자를 제거해도 예외가 나지 않는다 (경계 조건).

        Given: 빈 실패 목록
        When: 없는 일자를 제거
        Then: 예외 없이 빈 상태를 유지한다
        """
        # Given
        path = tmp_path / "failures.json"

        # When
        remove_failure(path, date(2019, 1, 3))

        # Then
        assert load_failures(path) == {}
