"""Test-only helpers for the affiliate programme: settings, secrets and signed
webhook calls through the real Flask app. Everything writes to the per-test
database from api/tests/conftest.py."""
import json
import time

from api import affiliate
from api.db import get_conn

ORDER = {"event": "order.paid", "order_id": "A-1001", "amount": "1000.00", "currency": "PLN"}


def set_affiliate_settings(**values):
    values = {"enabled": "1", **values}
    with get_conn() as conn:
        for name, value in values.items():
            conn.execute(
                """INSERT INTO settings (key, value) VALUES (?, ?)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                (f"affiliate_{name}", str(value)),
            )


def enable(admin):
    """Switch the programme on and return a fresh webhook secret."""
    set_affiliate_settings(shop_url="https://shop.example.com/")
    return affiliate.create_webhook_secret(admin)["secret"]


def client():
    import server
    return server.create_app().test_client()


def post_webhook(secret, payload, *, timestamp=None, signature=None, raw=None, headers=None,
                 address=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    sent = {"Content-Type": "application/json"}
    if signature is not False:
        sent[affiliate.SIGNATURE_HEADER] = signature or affiliate.sign(
            secret, body, timestamp if timestamp is not None else int(time.time()))
    sent.update(headers or {})
    environ = {"REMOTE_ADDR": address} if address else {}
    return client().post("/api/affiliate/webhook", data=body, headers=sent, environ_base=environ)


def conversions():
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM affiliate_conversions ORDER BY id").fetchall()]


def link_for(partner):
    with get_conn() as conn:
        code = "".join(c for c in f"p{partner['id']}xxxxxxxxxx".lower() if c.isalnum())[:10]
        return dict(conn.execute(
            "INSERT INTO affiliate_links (partner_id, code) VALUES (?, ?) RETURNING id, code",
            (partner["id"], code)).fetchone())


def login(call_api, user):
    _, body = call_api("POST", "/api/auth/login", body={
        "email": user["email"], "password": user["plain_password"], "role": user["role"]})
    return body["token"]
