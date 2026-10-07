"""scripts/db_tool.py: guards, routing and migration application, against a fake runner."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

import db_tool as db  # noqa: E402


class FakeRunner:
    name = "fake"

    def __init__(self, ok: bool = True) -> None:
        self.sql: list[str] = []
        self._ok = ok

    def run(self, sql: str) -> list[dict]:
        self.sql.append(sql)
        return [{"ok": self._ok}]


@pytest.mark.parametrize("sql", ["select 1", "  SELECT * from t where a = 'drop'", "with x as (select 1) select * from x",
                                 "explain select 1", "select 1;"])
def test_reads_are_allowed(sql: str) -> None:
    assert db.is_read_only(sql)


@pytest.mark.parametrize("sql", ["delete from t", "select 1; drop table t", "update t set a=1",
                                 "with d as (delete from t returning *) select * from d",
                                 "select * into new_t from t", "create table x()", "truncate t",
                                 "select pg_sleep(1); select 2"])
def test_writes_and_stacked_statements_are_refused(sql: str) -> None:
    assert not db.is_read_only(sql)


def test_query_refuses_a_write_and_never_calls_the_runner() -> None:
    runner = FakeRunner()
    with pytest.raises(db.DbError, match="read-only"):
        db.query(runner, "delete from t")
    assert runner.sql == []


def test_exec_needs_yes() -> None:
    runner = FakeRunner()
    with pytest.raises(db.DbError, match="--yes"):
        db.execute(runner, "delete from t", yes=False)
    assert runner.sql == []
    db.execute(runner, "delete from t", yes=True)
    assert runner.sql == ["delete from t"]


def test_migrations_selected_by_prefix_and_name() -> None:
    names = [p.name for p in db.migration_files(since="20261006")]
    assert names == ["20261006000001_detector_hits_kind.sql", "20261006000002_confluence_shadow.sql"]
    assert [p.name for p in db.migration_files(only=["supabase/migrations/20261006000002_confluence_shadow.sql"])] == [
        "20261006000002_confluence_shadow.sql"]
    with pytest.raises(db.DbError, match="not in supabase/migrations"):
        db.migration_files(only=["nope.sql"])


def test_dry_run_runs_nothing() -> None:
    runner = FakeRunner()
    lines = db.apply_files(runner, db.migration_files(since="20261006"), yes=False, dry_run=True)
    assert lines == ["would apply 20261006000001_detector_hits_kind.sql",
                     "would apply 20261006000002_confluence_shadow.sql"]
    assert runner.sql == []


def test_apply_needs_yes_and_records_each_file_in_order() -> None:
    runner = FakeRunner()
    files = db.migration_files(since="20261006")
    with pytest.raises(db.DbError, match="--yes"):
        db.apply_files(runner, files, yes=False, dry_run=False)
    assert runner.sql == []
    lines = db.apply_files(runner, files, yes=True, dry_run=False)
    assert lines == ["applied 20261006000001_detector_hits_kind.sql", "applied 20261006000002_confluence_shadow.sql"]
    assert runner.sql[0] == db.LEDGER_DDL
    assert "detector_hits" in runner.sql[1] and "applied_migrations" in runner.sql[2]
    assert "confluence_shadow" in runner.sql[3] and "20261006000002" in runner.sql[4]


def test_status_reports_each_recent_migration() -> None:
    assert db.status(FakeRunner(ok=True))["migrations"] == {k: True for k in db.EXPECTED_OBJECTS}
    assert not any(db.status(FakeRunner(ok=False))["migrations"].values())


def test_route_selection_and_missing_credentials() -> None:
    assert isinstance(db.build_runner(None, {"SUPABASE_URL": "https://abc.supabase.co", "SUPABASE_ACCESS_TOKEN": "t"}),
                      db.ApiRunner)
    assert isinstance(db.build_runner(None, {"DATABASE_URL": "postgresql://x"}), db.PostgresRunner)
    with pytest.raises(db.DbError, match="no usable database route"):
        db.build_runner(None, {})
    with pytest.raises(db.DbError):
        db.build_runner("postgres", {"SUPABASE_URL": "https://abc.supabase.co", "SUPABASE_ACCESS_TOKEN": "t"})


class _Resp:
    def __init__(self, code: int, body: object) -> None:
        self.status_code, self._body, self.text = code, body, str(body)

    def json(self) -> object:
        return self._body


class _Client:
    def __init__(self, resp: _Resp) -> None:
        self.resp, self.calls = resp, []

    def post(self, url: str, **kw: object) -> _Resp:
        self.calls.append((url, kw))
        return self.resp


def test_api_runner_posts_the_query_and_never_leaks_the_token_in_errors() -> None:
    client = _Client(_Resp(200, [{"a": 1}]))
    runner = db.ApiRunner("https://abcdef.supabase.co", "SECRET-TOKEN", client)
    assert runner.run("select 1") == [{"a": 1}]
    url, kw = client.calls[0]
    assert url == "https://api.supabase.com/v1/projects/abcdef/database/query"
    assert kw["json"] == {"query": "select 1"}
    with pytest.raises(db.DbError, match="expired or revoked") as err:
        db.ApiRunner("https://abcdef.supabase.co", "SECRET-TOKEN", _Client(_Resp(401, "x"))).run("select 1")
    assert "SECRET-TOKEN" not in str(err.value)


def test_main_returns_1_with_a_message_when_no_credentials(monkeypatch: pytest.MonkeyPatch,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    for var in ("SUPABASE_ACCESS_TOKEN", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    assert db.main(["status"]) == 1
    assert "no usable database route" in capsys.readouterr().err
