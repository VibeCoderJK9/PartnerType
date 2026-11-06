"""Producer-managed product catalogue exposed to partners by visibility flags."""

from psycopg import IntegrityError
from datetime import datetime

from api import audit
from api.authz import can_configure
from api.db import get_conn
from api.validation import (
    parse_allowed_fields,
    parse_boolean_flag,
    parse_enum,
    parse_finite_number,
    parse_integer_id,
    parse_non_negative_integer,
    parse_optional_string,
    parse_required_string,
)
from api.i18n import N_, t


PRODUCT_CATEGORIES = ("Wdrożenie", "Licencja", "Usługa", "Wsparcie")
# name_en is the producer's OWN English wording of a product he named himself
# (e.g. "ERP implementation", "CRM licence"), following the same *_en + Ui.producerLabel
# + Settings switch pattern already shipped for bonus_definitions, commission
# tiers and lead_stages. Empty means "not translated yet"; readers
# fall back to the Polish original through Ui.producerLabel rather than
# showing a blank name. The CATEGORY stays the closed PRODUCT_CATEGORIES enum
# below -- this column never touches it and no category_en exists.
PRODUCT_FIELDS = (
    "name", "name_en", "code", "category", "base_price", "offered", "active",
    "sort_order",
)
MAX_SORT_ORDER = 1_000_000


def _require_configure(user):
    """Refuse a write before anything is read or written.

    The HTTP handlers below already answer 403, but they are not the only
    caller: the assistant's confirmed actions and any future script reach
    these functions directly. Putting the check in the canonical writer means
    a new entry point cannot forget it.
    """
    if not can_configure(user):
        raise PermissionError(t("Access denied."))


def _validated_product_data(data, *, creating=False):
    clean = parse_allowed_fields(data, PRODUCT_FIELDS, N_("Product"))
    if creating or "name" in clean:
        clean["name"] = parse_required_string(
            clean.get("name"), N_("Product name"), max_length=200
        )
    if "name_en" in clean:
        clean["name_en"] = parse_optional_string(
            clean["name_en"], N_("English product name"), max_length=200
        )
    if creating or "category" in clean:
        clean["category"] = parse_enum(
            clean.get("category"), N_("Category"), PRODUCT_CATEGORIES
        )
    if "code" in clean:
        code = parse_optional_string(clean["code"], N_("Product code"), max_length=100)
        clean["code"] = code or None
    if "base_price" in clean:
        clean["base_price"] = (
            None
            if clean["base_price"] in (None, "")
            else parse_finite_number(
                clean["base_price"], N_("Base price"), minimum=0, maximum=1_000_000_000_000
            )
        )
    if "offered" in clean:
        clean["offered"] = parse_boolean_flag(clean["offered"], N_("Offered"))
    if "active" in clean:
        clean["active"] = parse_boolean_flag(clean["active"], N_("Active"))
    if "sort_order" in clean:
        clean["sort_order"] = parse_non_negative_integer(
            clean["sort_order"], N_("Order"), maximum=MAX_SORT_ORDER
        )
    return clean


def _integrity_error(exc):
    constraint = getattr(getattr(exc, "diag", None), "constraint_name", None)
    if constraint == "products_name_key":
        return ValueError(t("A product with this name already exists."))
    if constraint == "products_code_key":
        return ValueError(t("A product with this code already exists."))
    return ValueError(t("Could not save the product."))


def _ensure_casefold_unique(conn, *, name=None, code=None, exclude_id=None):
    """Enforce Unicode-aware uniqueness; SQLite NOCASE is ASCII-only."""
    rows = conn.execute("SELECT id, name, code FROM products").fetchall()
    for row in rows:
        if exclude_id is not None and row["id"] == exclude_id:
            continue
        if name is not None and row["name"].casefold() == name.casefold():
            raise ValueError(t("A product with this name already exists."))
        if code is not None and row["code"] and row["code"].casefold() == code.casefold():
            raise ValueError(t("A product with this code already exists."))


def get_all(user):
    conn = get_conn()
    try:
        if user["role"] == "partner":
            rows = conn.execute(
                "SELECT * FROM products WHERE offered=1 AND active=1 "
                "ORDER BY sort_order, lower(name), id"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM products ORDER BY sort_order, lower(name), id"
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_by_id(product_id):
    product_id = parse_integer_id(product_id, N_("Product ID"))
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_product(data, user):
    _require_configure(user)
    clean = _validated_product_data(data, creating=True)
    values = {
        "name": clean["name"],
        "name_en": clean.get("name_en", ""),
        "code": clean.get("code"),
        "category": clean["category"],
        "base_price": clean.get("base_price"),
        "offered": clean.get("offered", 0),
        "active": clean.get("active", 1),
        "sort_order": clean.get("sort_order", 0),
    }
    conn = get_conn()
    try:
        _ensure_casefold_unique(conn, name=values["name"], code=values["code"])
        try:
            cursor = conn.execute(
                """INSERT INTO products
                   (name, name_en, code, category, base_price, offered, active, sort_order, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    values["name"], values["name_en"], values["code"], values["category"],
                    values["base_price"], values["offered"], values["active"],
                    values["sort_order"], datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
        except IntegrityError as exc:
            raise _integrity_error(exc)
        product_id = cursor.lastrowid
    finally:
        conn.close()
    product = get_by_id(product_id)
    audit.log(user, "product.create", "product", str(product_id), f"∅ → {product}")
    return product


def update_product(product_id, data, user):
    _require_configure(user)
    product_id = parse_integer_id(product_id, N_("Product ID"))
    clean = _validated_product_data(data)
    if not clean:
        raise ValueError(t("No data to update."))
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if not row:
            raise ValueError(t("The product does not exist."))
        previous = dict(row)
        _ensure_casefold_unique(
            conn,
            name=clean.get("name"),
            code=clean.get("code") if "code" in clean else None,
            exclude_id=product_id,
        )
        changed = {
            key: value for key, value in clean.items() if previous.get(key) != value
        }
        if changed:
            assignments = ", ".join(f"{key}=?" for key in changed)
            try:
                conn.execute(
                    f"UPDATE products SET {assignments} WHERE id=?",
                    [*changed.values(), product_id],
                )
                conn.commit()
            except IntegrityError as exc:
                raise _integrity_error(exc)
    finally:
        conn.close()
    product = get_by_id(product_id)
    if changed:
        before = {key: previous.get(key) for key in changed}
        after = {key: product.get(key) for key in changed}
        audit.log(
            user, "product.update", "product", str(product_id), f"{before} → {after}"
        )
    return product


def delete_product(product_id, user):
    _require_configure(user)
    product_id = parse_integer_id(product_id, N_("Product ID"))
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if not row:
            raise ValueError(t("The product does not exist."))
        previous = dict(row)
        references = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE product_id=?", (product_id,)
        ).fetchone()[0]
        if previous["active"]:
            conn.execute("UPDATE products SET active=0 WHERE id=?", (product_id,))
            conn.commit()
    finally:
        conn.close()
    product = get_by_id(product_id)
    if previous["active"]:
        audit.log(
            user,
            "product.delete",
            "product",
            str(product_id),
            t("active: 1 → 0; linked leads: {leads}", leads=references),
            detail_key="audit_detail_product_archived",
            detail_params={"leads": references},
        )
    return product


def handle_list(handler, user, qs):
    products = get_all(user)
    return 200, {"products": products, "total": len(products)}


def handle_create(handler, user, body):
    if not can_configure(user):
        return 403, {"error": t("Access denied.")}
    try:
        return 201, {"product": create_product(body, user)}
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_update(handler, user, product_id, body):
    if not can_configure(user):
        return 403, {"error": t("Access denied.")}
    try:
        return 200, {"product": update_product(product_id, body, user)}
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_delete(handler, user, product_id):
    if not can_configure(user):
        return 403, {"error": t("Access denied.")}
    try:
        return 200, {"product": delete_product(product_id, user), "ok": True}
    except ValueError as exc:
        return 400, {"error": str(exc)}
