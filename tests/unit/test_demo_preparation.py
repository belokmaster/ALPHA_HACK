"""Explicit demo preparation cannot silently target the shared working database."""

from argparse import Namespace

import pytest

from scripts.prepare_integration import preparation_url


def test_default_target_remains_isolated(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+asyncpg://localhost/alfa_test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/working")
    args = Namespace(shared_demo=False, apply=True, demo_grant="alice")
    assert preparation_url(args).endswith("/alfa_test")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+asyncpg://localhost/working")
    with pytest.raises(SystemExit, match="isolated"):
        preparation_url(args)


@pytest.mark.parametrize(
    "demo,apply,grant",
    [(False, True, "alice"), (True, False, "alice"), (True, True, None)],
)
def test_shared_demo_requires_all_explicit_conditions(monkeypatch, demo, apply, grant):
    monkeypatch.setenv("DEMO_MODE", str(demo))
    with pytest.raises(SystemExit, match="requires"):
        preparation_url(Namespace(shared_demo=True, apply=apply, demo_grant=grant))


def test_shared_demo_uses_existing_settings_target(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/working")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+asyncpg://localhost/alfa_test")
    assert preparation_url(Namespace(shared_demo=True, apply=True, demo_grant="alice")) == (
        "postgresql+asyncpg://localhost/working"
    )
