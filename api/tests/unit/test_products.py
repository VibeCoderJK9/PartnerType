"""Product catalogue business rules and lead association tests."""

import pytest

from api import audit
from api.db import get_conn
from api.leads import create_lead, get_lead, get_leads, handle_create as handle_lead_create
from api.products import (
    create_product,
    delete_product,
    get_all,
    handle_create,
    handle_delete,
    handle_update,
    update_product,
)


def _product(**overrides):
    data = {
        "name": "Wdrożenie ERP",
        "code": "ERP-01",
        "category": "Wdrożenie",
        "base_price": 12500,
        "offered": True,
        "active": True,
        "sort_order": 2,
    }
    data.update(overrides)
    return data


def _lead(**overrides):
    data = {
        "client_name": "Anna Testowa",
        "client_email": "anna@example.pl",
        "company": "Testowa Sp. z o.o.",
        "value": 10000,
    }
    data.update(overrides)
    return data


class TestProductCrud:
    def test_create_persists_validated_catalogue_row(self, admin_user):
        product = create_product(_product(), admin_user)

        assert product["name"] == "Wdrożenie ERP"
        assert product["code"] == "ERP-01"
        assert product["category"] == "Wdrożenie"
        assert product["base_price"] == 12500
        assert product["offered"] == 1
        assert product["active"] == 1
        assert product["sort_order"] == 2

    def test_name_is_unique_case_insensitively(self, admin_user):
        create_product(_product(), admin_user)

        with pytest.raises(ValueError, match="nazwie"):
            create_product(_product(name="  WDROŻENIE erp  ", code="ERP-02"), admin_user)

    def test_nonempty_code_is_unique_case_insensitively(self, admin_user):
        create_product(_product(), admin_user)

        with pytest.raises(ValueError, match="kodzie"):
            create_product(_product(name="Inny", code="erp-01"), admin_user)

    @pytest.mark.parametrize("category", ["", "Hardware", None])
    def test_category_is_bounded(self, admin_user, category):
        with pytest.raises(ValueError, match="Kategoria"):
            create_product(_product(category=category), admin_user)

    @pytest.mark.parametrize("price", [-1, float("inf"), "dużo"])
    def test_base_price_must_be_non_negative_and_finite(self, admin_user, price):
        with pytest.raises(ValueError, match="Cena bazowa"):
            create_product(_product(base_price=price), admin_user)

    def test_update_records_old_and_new_offered_value(self, admin_user):
        product = create_product(_product(offered=True), admin_user)
        audit.log.reset_mock()

        updated = update_product(product["id"], {"offered": False}, admin_user)

        assert updated["offered"] == 0
        args = audit.log.call_args.args
        assert args[1:4] == ("product.update", "product", str(product["id"]))
        assert "offered" in args[4]
        assert "1" in args[4] and "0" in args[4]

    def test_delete_is_soft_even_when_leads_reference_product(self, admin_user, partner_user):
        product = create_product(_product(), admin_user)
        lead = create_lead(partner_user, _lead(product_id=product["id"], product="spoof"))

        delete_product(product["id"], admin_user)

        conn = get_conn()
        try:
            stored_product = conn.execute(
                "SELECT active FROM products WHERE id=?", (product["id"],)
            ).fetchone()
            stored_lead = conn.execute(
                "SELECT product_id, product FROM leads WHERE id=?", (lead["id"],)
            ).fetchone()
        finally:
            conn.close()
        assert stored_product["active"] == 0
        assert stored_lead["product_id"] == product["id"]
        assert stored_lead["product"] == "Wdrożenie ERP"


class TestProductVisibility:
    def test_admin_sees_all_rows_but_partner_only_offered_active(self, admin_user, partner_user):
        visible = create_product(_product(name="Widoczny", code="V", offered=True), admin_user)
        create_product(_product(name="Ukryty", code="H", offered=False), admin_user)
        inactive = create_product(_product(name="Nieaktywny", code="I", offered=True), admin_user)
        delete_product(inactive["id"], admin_user)

        assert len(get_all(admin_user)) == 3
        assert [row["id"] for row in get_all(partner_user)] == [visible["id"]]


class TestProductPermissions:
    def test_partner_cannot_mutate_catalogue(self, admin_user, partner_user):
        product = create_product(_product(), admin_user)

        assert handle_create(None, partner_user, _product())[0] == 403
        assert handle_update(None, partner_user, product["id"], {"name": "Nie"})[0] == 403
        assert handle_delete(None, partner_user, product["id"])[0] == 403


class TestLeadProductSelection:
    def test_valid_selection_persists_fk_and_catalogue_name_snapshot(self, admin_user, partner_user):
        product = create_product(_product(), admin_user)

        lead = create_lead(
            partner_user,
            _lead(product_id=product["id"], product="Podsunięty tekst"),
        )

        assert lead["product_id"] == product["id"]
        assert lead["product"] == "Wdrożenie ERP"

    @pytest.mark.parametrize("state", ["hidden", "inactive", "missing"])
    def test_hidden_inactive_or_missing_product_is_rejected(
        self, admin_user, partner_user, state
    ):
        product_id = 999999
        if state != "missing":
            product = create_product(
                _product(offered=state != "hidden", active=state != "inactive"),
                admin_user,
            )
            product_id = product["id"]

        with pytest.raises(ValueError, match="oferowany"):
            create_lead(partner_user, _lead(product_id=product_id))

    def test_http_submission_requires_product_id(self, partner_user):
        status, body = handle_lead_create(
            None,
            partner_user,
            _lead(nip="1111111111", website="nova-sklep.pl", product="ERP", quantity=1),
        )

        assert status == 400
        assert "Produkt" in body["error"]


class TestLeadProductEnglishProjection:
    """product_en is the catalogue's CURRENT English name for a
    lead's product_id -- a live projection, not a snapshot like
    bonus_awards.name_en. leads.product (the Polish text persisted at
    submission) is never rewritten by it."""

    def test_lead_carries_the_products_current_english_name(self, admin_user, partner_user):
        product = create_product(_product(name_en="Rollout package"), admin_user)
        lead = create_lead(
            partner_user, _lead(product_id=product["id"], product="Podsunięty tekst")
        )

        read_back = get_lead(lead["id"], admin_user)

        assert read_back["product"] == "Wdrożenie ERP"
        assert read_back["product_en"] == "Rollout package"

    def test_product_en_is_the_catalogues_current_name_not_a_historical_snapshot(
        self, admin_user, partner_user
    ):
        product = create_product(_product(name_en="Rollout package"), admin_user)
        lead = create_lead(partner_user, _lead(product_id=product["id"]))

        update_product(product["id"], {"name_en": "Deployment package"}, admin_user)

        read_back = get_lead(lead["id"], admin_user)
        assert read_back["product_en"] == "Deployment package"
        # The Polish text recorded at submission time is untouched by a
        # later rename -- only the live EN projection moves.
        assert read_back["product"] == "Wdrożenie ERP"

    def test_archived_product_still_projects_its_english_name(self, admin_user, partner_user):
        product = create_product(_product(name_en="Rollout package"), admin_user)
        lead = create_lead(partner_user, _lead(product_id=product["id"]))

        delete_product(product["id"], admin_user)  # soft delete: active -> 0

        read_back = get_lead(lead["id"], admin_user)
        assert read_back["product_en"] == "Rollout package"

    def test_missing_product_relation_falls_back_to_the_historical_polish_text(
        self, admin_user, partner_user
    ):
        # Predates the product catalogue entirely: no product_id at all, only
        # the free-text product name a partner typed before it existed.
        lead = create_lead(partner_user, _lead(product="Stare zgłoszenie"))

        read_back = get_lead(lead["id"], admin_user)
        assert read_back["product_en"] == ""
        assert read_back["product"] == "Stare zgłoszenie"

    def test_list_projection_carries_the_same_english_name(self, admin_user, partner_user):
        product = create_product(_product(name_en="Rollout package"), admin_user)
        lead = create_lead(partner_user, _lead(product_id=product["id"]))

        rows = get_leads(admin_user)
        found = next(row for row in rows if row["id"] == lead["id"])
        assert found["product_en"] == "Rollout package"

