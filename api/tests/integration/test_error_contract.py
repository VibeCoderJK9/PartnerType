"""Public error-boundary regression tests.

Unexpected exception messages are attacker-controlled diagnostic material: they may
contain credentials, SQL, filesystem paths, or request values.  These tests prove
that only correlation metadata crosses the router and bulk-item boundaries.
"""

import json
import logging

import pytest

from api import leads, partner_bonuses


SENSITIVE_MARKERS = (
    "token=tok_live_router_secret",
    "password=CorrectHorseBatteryStaple",
    "SELECT secret FROM users WHERE email='victim@example.test'",
    "/srv/partnertype/private/config.py",
    "smtp://mailer:smtp-secret@mail.example.test",
    "api_key=sk_test_partnertype_secret",
)

GENERIC_ERROR = "Wystąpił nieoczekiwany błąd serwera."


def _rendered_logs(caplog):
    return "\n".join(record.getMessage() for record in caplog.records)


def _raise_sensitive(marker):
    raise RuntimeError(marker)


@pytest.mark.parametrize("marker", SENSITIVE_MARKERS)
def test_router_unexpected_error_is_correlated_and_redacted(
    call_api_details, monkeypatch, caplog, marker
):
    def failing_login(handler, body):
        _raise_sensitive(marker)

    monkeypatch.setattr("api.router.handle_login", failing_login)

    with caplog.at_level(logging.ERROR):
        status, body, _, _ = call_api_details(
            "POST",
            "/api/auth/login",
            body={"email": marker, "password": marker},
        )

    assert status == 500
    assert body["error"] == GENERIC_ERROR
    assert body["request_id"]

    client_json = json.dumps(body, ensure_ascii=False)
    rendered = _rendered_logs(caplog)
    assert marker not in client_json
    assert marker not in rendered
    assert f"request_id={body['request_id']}" in rendered
    assert "exception=RuntimeError" in rendered
    assert "method=POST" in rendered
    assert "route=auth_login" in rendered
    assert "_raise_sensitive:" in rendered


@pytest.mark.parametrize("exc_type", [ValueError, PermissionError])
def test_leads_bulk_preserves_expected_domain_detail(
    # Bulk delegates to the same canonical decision as the single-lead route.
    test_db_path, monkeypatch, exc_type
):
    monkeypatch.setattr(
        leads,
        "apply_registration_action",
        lambda *args, **kwargs: (_ for _ in ()).throw(exc_type("Bezpieczny opis domenowy.")),
    )
    monkeypatch.setattr(leads.audit, "log", lambda *args, **kwargs: None)

    status, body = leads.handle_bulk(
        None, {"id": 1, "role": "admin"}, {"action": "accept", "items": [
            {"id": "LD-1", "expected_revision": 0, "operation_id": "domain"}]}
    )

    assert status == 200
    assert body["error"] == [
        {"id": "LD-1", "reason": "Bezpieczny opis domenowy.",
         "status": 403 if exc_type is PermissionError else 400,
         "code": "forbidden" if exc_type is PermissionError else "invalid_request"}
    ]


def test_leads_bulk_unexpected_error_is_correlated_and_redacted(
    test_db_path, monkeypatch, caplog
):
    marker = "password=bulk-lead-secret"
    monkeypatch.setattr(
        leads,
        "apply_registration_action",
        lambda *args, **kwargs: _raise_sensitive(marker),
    )
    monkeypatch.setattr(leads.audit, "log", lambda *args, **kwargs: None)

    with caplog.at_level(logging.ERROR):
        status, body = leads.handle_bulk(
            None,
            {"id": 1, "role": "admin"},
            {"action": "accept", "items": [{"id": "LD-1", "expected_revision": 0, "operation_id": "error"}]},
        )

    item = body["error"][0]
    assert status == 200
    assert item["id"] == "LD-1"
    assert item["code"] == "unexpected_error"
    assert item["request_id"]
    assert marker not in json.dumps(body, ensure_ascii=False)
    assert marker not in _rendered_logs(caplog)
    assert f"request_id={item['request_id']}" in _rendered_logs(caplog)
    assert "route=leads_bulk" in _rendered_logs(caplog)


@pytest.mark.parametrize("exc_type", [ValueError, PermissionError])
def test_partner_bonus_bulk_preserves_expected_domain_detail(monkeypatch, exc_type):
    monkeypatch.setattr(
        partner_bonuses,
        "assign_bonus",
        lambda *args, **kwargs: (_ for _ in ()).throw(exc_type("Bezpieczny bonus.")),
    )
    monkeypatch.setattr(partner_bonuses.audit, "log", lambda *args, **kwargs: None)

    result = partner_bonuses.bulk_assign([9], 3, None, {"id": 1, "role": "admin"})

    assert result["errors"] == [{"partner_id": 9, "error": "Bezpieczny bonus."}]


def test_partner_bonus_bulk_unexpected_error_is_correlated_and_redacted(
    monkeypatch, caplog
):
    marker = "api_key=bulk-bonus-secret"
    monkeypatch.setattr(
        partner_bonuses,
        "assign_bonus",
        lambda *args, **kwargs: _raise_sensitive(marker),
    )
    monkeypatch.setattr(partner_bonuses.audit, "log", lambda *args, **kwargs: None)

    with caplog.at_level(logging.ERROR):
        result = partner_bonuses.bulk_assign(
            [9], 3, None, {"id": 1, "role": "admin"}
        )

    item = result["errors"][0]
    assert item["partner_id"] == 9
    assert item["code"] == "unexpected_error"
    assert item["request_id"]
    assert marker not in json.dumps(result, ensure_ascii=False)
    assert marker not in _rendered_logs(caplog)
    assert f"request_id={item['request_id']}" in _rendered_logs(caplog)
    assert "route=partner_bonus_bulk_assign" in _rendered_logs(caplog)
