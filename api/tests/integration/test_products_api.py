"""Router-level product catalogue contract."""

from api.db import get_conn


def _login_token(call_api, user):
    _, body = call_api(
        "POST",
        "/api/auth/login",
        body={
            "email": user["email"],
            "password": user["plain_password"],
            "role": user["role"],
        },
    )
    return body["token"]


def _payload(**overrides):
    body = {
        "name": "Pakiet wdrożeniowy",
        "code": "WD-01",
        "category": "Wdrożenie",
        "base_price": 9900,
        "offered": True,
        "active": True,
        "sort_order": 0,
    }
    body.update(overrides)
    return body


def test_admin_crud_and_partner_visibility_are_enforced(
    call_api, admin_user, partner_user
):
    admin_token = _login_token(call_api, admin_user)
    partner_token = _login_token(call_api, partner_user)

    status, body = call_api("POST", "/api/products", body=_payload(), token=admin_token)
    assert status == 201
    product_id = body["product"]["id"]

    status, body = call_api(
        "POST",
        "/api/products",
        body=_payload(name="Ukryty", code="H-01", offered=False),
        token=admin_token,
    )
    assert status == 201

    # Integration fixtures seed product id=1 for the pre-existing lead workflow
    # tests, plus the two rows created above.
    assert call_api("GET", "/api/products", token=admin_token)[1]["total"] == 3
    partner_body = call_api("GET", "/api/products", token=partner_token)[1]
    partner_ids = {p["id"] for p in partner_body["products"]}
    assert product_id in partner_ids
    assert all(p["name"] != "Ukryty" for p in partner_body["products"])

    status, body = call_api(
        "PATCH",
        f"/api/products/{product_id}",
        body={"offered": False},
        token=admin_token,
    )
    assert status == 200 and body["product"]["offered"] == 0
    assert all(
        p["id"] != product_id
        for p in call_api("GET", "/api/products", token=partner_token)[1]["products"]
    )

    status, _ = call_api("DELETE", f"/api/products/{product_id}", token=admin_token)
    assert status == 200
    conn = get_conn()
    try:
        assert conn.execute("SELECT active FROM products WHERE id=?", (product_id,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_partner_mutations_are_forbidden(call_api, partner_user):
    token = _login_token(call_api, partner_user)
    assert call_api("POST", "/api/products", body=_payload(), token=token)[0] == 403
    assert call_api("PATCH", "/api/products/1", body={"offered": False}, token=token)[0] == 403
    assert call_api("DELETE", "/api/products/1", token=token)[0] == 403


def test_product_name_en_survives_create_read_patch_read(call_api, admin_user):
    """the producer's own English wording of a product name is a
    second, optional column -- the ID and the enum category it was created
    with never change underneath it."""
    admin_token = _login_token(call_api, admin_user)

    status, body = call_api(
        "POST", "/api/products", body=_payload(name_en="Rollout package"), token=admin_token
    )
    assert status == 201
    product = body["product"]
    assert product["name_en"] == "Rollout package"
    product_id = product["id"]
    original_category = product["category"]

    status, body = call_api("GET", "/api/products", token=admin_token)
    reread = next(p for p in body["products"] if p["id"] == product_id)
    assert reread["name_en"] == "Rollout package"
    assert reread["category"] == original_category

    status, body = call_api(
        "PATCH", f"/api/products/{product_id}",
        body={"name_en": "Deployment package"}, token=admin_token,
    )
    assert status == 200
    assert body["product"]["name_en"] == "Deployment package"
    # The ID and category are untouched by an EN-only edit.
    assert body["product"]["id"] == product_id
    assert body["product"]["category"] == original_category

    status, body = call_api("GET", "/api/products", token=admin_token)
    reread_again = next(p for p in body["products"] if p["id"] == product_id)
    assert reread_again["name_en"] == "Deployment package"


def test_product_name_en_is_optional_and_defaults_empty(call_api, admin_user):
    admin_token = _login_token(call_api, admin_user)

    status, body = call_api("POST", "/api/products", body=_payload(), token=admin_token)
    assert status == 201
    assert body["product"]["name_en"] == ""


def test_product_category_stays_a_closed_enum_over_http(call_api, admin_user):
    admin_token = _login_token(call_api, admin_user)

    status, body = call_api(
        "POST", "/api/products",
        body=_payload(category="Nieznana kategoria"), token=admin_token,
    )
    assert status == 400
    assert "Kategoria" in body["error"]


def test_lead_rejects_manually_supplied_hidden_product_id(
    call_api, admin_user, partner_user
):
    admin_token = _login_token(call_api, admin_user)
    partner_token = _login_token(call_api, partner_user)
    _, product_body = call_api(
        "POST", "/api/products", body=_payload(offered=False), token=admin_token
    )

    status, body = call_api(
        "POST",
        "/api/leads",
        body={
            "client_name": "Klient",
            "client_email": "klient@example.pl",
            "company": "Firma",
            "value": 1000,
            "nip": "1111111111",
            "website": "firma.pl",
            "product_id": product_body["product"]["id"],
            "product": "Fałszywa nazwa",
            "quantity": 1,
        },
        token=partner_token,
    )

    assert status == 400
    assert "oferowany" in body["error"]
