from http.cookies import SimpleCookie

from api.db import get_conn
from api.auth import _hash_session_token
from api.http_security import SESSION_COOKIE_NAME


def _cookie_pair(set_cookie_header):
    cookie = SimpleCookie()
    cookie.load(set_cookie_header)
    return f"{SESSION_COOKIE_NAME}={cookie[SESSION_COOKIE_NAME].value}"


def test_cookie_login_me_logout_lifecycle(call_api_details, partner_user):
    login_status, login_body, login_headers, _ = call_api_details(
        "POST",
        "/api/auth/login",
        body={
            "email": partner_user["email"],
            "password": partner_user["plain_password"],
            "role": "partner",
        },
    )

    assert login_status == 200
    assert set(login_body) == {"user"}
    assert login_body["user"]["email"] == partner_user["email"]
    set_cookie = login_headers["Set-Cookie"]
    assert "HttpOnly" in set_cookie
    assert "SameSite=Strict" in set_cookie
    assert "Path=/" in set_cookie
    cookie_header = _cookie_pair(set_cookie)

    me_status, me_body, _, _ = call_api_details(
        "GET", "/api/auth/me", headers={"Cookie": cookie_header}
    )
    assert me_status == 200
    assert me_body["user"]["id"] == partner_user["id"]

    token = cookie_header.split("=", 1)[1]
    conn = get_conn()
    try:
        before = conn.execute(
            "SELECT expires_at FROM sessions WHERE token = ?", (_hash_session_token(token),)
        ).fetchone()["expires_at"]
    finally:
        conn.close()
    call_api_details("GET", "/api/auth/me", headers={"Cookie": cookie_header})
    conn = get_conn()
    try:
        after = conn.execute(
            "SELECT expires_at FROM sessions WHERE token = ?", (_hash_session_token(token),)
        ).fetchone()["expires_at"]
    finally:
        conn.close()
    assert after >= before

    logout_status, logout_body, logout_headers, _ = call_api_details(
        "POST", "/api/auth/logout", headers={"Cookie": cookie_header}
    )
    assert logout_status == 200
    assert logout_body == {"ok": True}
    assert "Max-Age=0" in logout_headers["Set-Cookie"]

    reused_status, _, _, _ = call_api_details(
        "GET", "/api/auth/me", headers={"Cookie": cookie_header}
    )
    assert reused_status == 401


def test_authorization_bearer_is_not_an_authentication_path(
    call_api_details, partner_user
):
    _, _, login_headers, _ = call_api_details(
        "POST",
        "/api/auth/login",
        body={
            "email": partner_user["email"],
            "password": partner_user["plain_password"],
            "role": "partner",
        },
    )
    token = _cookie_pair(login_headers["Set-Cookie"]).split("=", 1)[1]
    status, _, _, _ = call_api_details(
        "GET", "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert status == 401


def test_login_cookie_is_secure_outside_local_development(
    call_api_details, partner_user, monkeypatch
):
    monkeypatch.setenv("PARTNERTYPE_ENV", "production")
    status, _, headers, _ = call_api_details(
        "POST",
        "/api/auth/login",
        body={
            "email": partner_user["email"],
            "password": partner_user["plain_password"],
            "role": "partner",
        },
    )
    assert status == 200
    assert "Secure" in headers["Set-Cookie"]
