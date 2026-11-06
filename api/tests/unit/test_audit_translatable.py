"""
PartnerType -- audit entries that can be read in either language.

An audit entry is written when something happens and read much later. The
producer may have switched the app to English in between, so a detail that is
a SENTENCE is stored as a dictionary key plus its values rather than as
finished Polish. A detail made of names, ids and figures is stored as-is:
it reads the same either way, and inventing a key for it would only add a
place for the two to disagree.
"""
import pytest

from api import audit
from api.db import get_conn

# Captured at import time, before the autouse mute_side_effects fixture
# replaces api.audit.log: an audit write is a side effect nothing else in the
# suite wants, but these tests are ABOUT that write.
_REAL_AUDIT_LOG = audit.log


@pytest.fixture(autouse=True)
def real_audit_log(mocker):
    """Undo the global mute, for this module only."""
    mocker.patch("api.audit.log", new=_REAL_AUDIT_LOG)


def _last_entry():
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


class TestTranslatableDetails:
    def test_a_sentence_detail_stores_its_key_and_values(self, admin_user):
        audit.log(admin_user, "product.delete", "product", "7",
                  "active: 1 → 0; powiązane leady: 3",
                  detail_key="audit_detail_product_archived",
                  detail_params={"leads": 3})

        entry = _last_entry()

        assert entry["detail_key"] == "audit_detail_product_archived"
        assert entry["detail_params"] == '{"leads":3}'
        # The Polish text stays: it is what an entry written before this
        # carried, and the only thing to fall back on.
        assert "powiązane leady: 3" in entry["detail"]

    def test_a_plain_data_detail_carries_no_key(self, admin_user):
        audit.log(admin_user, "lead.create", "lead", "LD-001",
                  "Anna Nowak / Acme / 2,000 PLN")

        entry = _last_entry()

        assert entry["detail_key"] == ""
        assert entry["detail_params"] == ""
        assert entry["detail"] == "Anna Nowak / Acme / 2,000 PLN"

    @pytest.mark.parametrize("params", [
        {"leads": {"nested": "object"}},
        {"leads": ["list"]},
        {"leads": True},
    ])
    def test_a_value_that_is_not_simple_text_is_dropped_not_stored(
        self, admin_user, params
    ):
        """audit.log must never raise -- it would take the real operation down
        with it. A value it cannot store is left out; the Polish detail is
        still there to read."""
        audit.log(admin_user, "product.delete", "product", "7", "szczegół",
                  detail_key="audit_detail_product_archived", detail_params=params)

        entry = _last_entry()

        assert entry["detail_params"] == "{}"
        assert entry["detail"] == "szczegół"

    def test_reading_decodes_the_values_into_an_object(self, admin_user):
        audit.log(admin_user, "bonus.bulk_assign", "partner", "",
                  "3 partnerów, bonus_def_id=13",
                  detail_key="audit_detail_bulk_bonus",
                  detail_params={"count": 3, "bonus_id": 13})

        _, entries = audit.get_logs(limit=1)

        assert entries[0]["detail_params"] == {"count": 3, "bonus_id": 13}

    def test_an_entry_written_before_this_reads_back_with_empty_values(
        self, admin_user
    ):
        audit.log(admin_user, "user.login", "user", "1", "")

        _, entries = audit.get_logs(limit=1)

        assert entries[0]["detail_key"] == ""
        assert entries[0]["detail_params"] == {}
