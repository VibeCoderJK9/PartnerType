"""Installations created under the old name keep their PARTNERHUB_* variables working."""
import logging

from api import env_compat


def test_an_old_name_fills_the_new_one_and_warns_once(caplog):
    environ = {"PARTNERHUB_ENV": "production", "PARTNERHUB_SMTP_HOST": "smtp.example.test"}
    with caplog.at_level(logging.WARNING, logger="partnertype.config"):
        seen = env_compat.apply_legacy_names(environ)
    assert environ["PARTNERTYPE_ENV"] == "production"
    assert environ["PARTNERTYPE_SMTP_HOST"] == "smtp.example.test"
    assert seen == ["PARTNERHUB_ENV", "PARTNERHUB_SMTP_HOST"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "\n" not in warnings[0].getMessage()
    assert "PARTNERHUB_ENV" in warnings[0].getMessage() and "PARTNERTYPE_" in warnings[0].getMessage()


def test_the_new_name_wins_when_both_are_set():
    environ = {"PARTNERHUB_ENV": "staging", "PARTNERTYPE_ENV": "production"}
    env_compat.apply_legacy_names(environ)
    assert environ["PARTNERTYPE_ENV"] == "production"


def test_no_old_names_no_warning(caplog):
    environ = {"PARTNERTYPE_ENV": "production"}
    with caplog.at_level(logging.WARNING, logger="partnertype.config"):
        assert env_compat.apply_legacy_names(environ) == []
    assert not caplog.records


def test_secret_files_keep_working_under_the_old_name(tmp_path):
    secret = tmp_path / "session_secret"
    secret.write_text("x" * 40)
    environ = {"PARTNERHUB_SESSION_SECRET_FILE": str(secret)}
    env_compat.apply_legacy_names(environ)
    assert environ["PARTNERTYPE_SESSION_SECRET_FILE"] == str(secret)


def test_the_package_applies_it_on_import():
    import api
    assert api._apply_legacy_names is env_compat.apply_legacy_names
