"""Real-router regression for the comment-read ownership rule."""
import pytest

from api.comments import create_comment, list_comments


def login(call_api, user):
    status, body = call_api("POST", "/api/auth/login", body={
        "email": user["email"], "password": user["plain_password"],
        "role": user["role"],
    })
    assert status == 200, body
    return body["token"]


def test_other_partner_cannot_read_public_comments(
    call_api, sample_lead, admin_user, partner_user_bronze,
):
    create_comment(sample_lead["id"], admin_user, "Private customer context")
    token = login(call_api, partner_user_bronze)
    status, body = call_api(
        "GET", f"/api/leads/{sample_lead['id']}/comments", token=token,
    )
    # Row-level security (0016): another partner's lead does not exist for him.
    assert status == 404
    assert "comments" not in body
    assert "Private customer context" not in str(body)


def test_owner_sees_public_but_not_internal_comments(
    call_api, sample_lead, admin_user, partner_user,
):
    create_comment(sample_lead["id"], admin_user, "Public request")
    create_comment(sample_lead["id"], admin_user, "Staff note", True)
    status, body = call_api(
        "GET", f"/api/leads/{sample_lead['id']}/comments",
        token=login(call_api, partner_user),
    )
    assert status == 200
    assert [row["body"] for row in body["comments"]] == ["Public request"]


@pytest.mark.parametrize("role", ["admin", "manager", "observer"])
def test_read_role_projection(sample_lead, admin_user, role):
    create_comment(sample_lead["id"], admin_user, "Public request")
    create_comment(sample_lead["id"], admin_user, "Staff note", True)
    user = dict(admin_user, role=role)
    comments = list_comments(sample_lead["id"], user)
    expected = ["Public request", "Staff note"] if role != "observer" else ["Public request"]
    assert [row["body"] for row in comments] == expected


@pytest.mark.parametrize("user", [None, {}, {"id": 1, "role": "unknown"}])
def test_domain_read_fails_closed(sample_lead, user):
    with pytest.raises(PermissionError):
        list_comments(sample_lead["id"], user)


def test_missing_lead_returns_404(call_api, admin_user):
    status, body = call_api(
        "GET", "/api/leads/LD-NOTFOUND/comments", token=login(call_api, admin_user),
    )
    assert status == 404
    assert "comments" not in body


def test_anonymous_read_requires_login(call_api, sample_lead):
    status, _ = call_api("GET", f"/api/leads/{sample_lead['id']}/comments")
    assert status == 401
