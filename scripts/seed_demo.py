"""
PartnerType — Repeatable Demo Dataset

`api.db.seed_db()` is one-shot: it no-ops the instant the `users` table has a
single row, so it can only ever run once in a database's life. That is wrong
for a live-demo reset, which must be run again before every showing without
manual cleanup. This script is the re-runnable replacement: it owns a small,
deliberate set of demo rows -- identified solely by a fixed email domain --
tears exactly those rows down, and rebuilds them the same way every time.
`api.db.seed_db()` itself is never touched or called.

Every seeded account, lead and decision is created through the real
application code paths (`api.users.create_user`, `api.leads.create_lead`,
`api.leads.update_lead`, `api.partner_bonuses.update_tier`,
`api.partner_bonuses.assign_bonus`, `api.bonus_awards.grant`,
`api.settings_api.set_many`) so the
resulting queue positions and commissions are exactly what the running app
would produce -- a wrong number here is a real backend bug, not a fixture
artefact.

Usage:
    python3 scripts/seed_demo.py
"""
import os
import sys
from datetime import datetime, timedelta

# Make the project root importable regardless of the caller's cwd (mirrors
# server.py's own sys.path setup) -- must run before any `api.*` import.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import commission_tiers  # noqa: E402
from api import db  # noqa: E402
from api import lead_stages  # noqa: E402
from api import bonus_awards  # noqa: E402
from api import bonus_defs  # noqa: E402
from api import partner_bonuses  # noqa: E402
from api import settings_api  # noqa: E402
from api.db import get_conn  # noqa: E402
from api.postgres import system_context  # noqa: E402
from api.leads import create_lead, update_lead  # noqa: E402
from api.users import create_user  # noqa: E402

# ─── Ownership marker ────────────────────────────────────
# Every account this script creates lives on this one domain. Teardown scans
# for it; a future deployment check can grep for it to strip demo accounts
# before going live. ".test" is IANA-reserved for testing (RFC 2606) and can
# never resolve to a real mailbox, so seeded credentials are unambiguous.
DEMO_EMAIL_DOMAIN = "demo.partnertype.test"
DEMO_PASSWORD = "Demo2026!"

# ─── Duplicate-queue settings restored on every reset ───────
DUPLICATE_POLICY = "queue"
QUEUE_SHARES = "100,50,25,10,0"
QUEUE_MAX = "5"

# ─── Accounts ──────────────────────────────────────────────────
# One producer (admin) plus one partner per tier. Diamond is intentionally
# skipped: the legacy `users.level` column's CHECK constraint only accepts
# bronze/silver/gold/platinum, and a seeded partner's level and tier_id must
# describe the same standing -- a Diamond partner has no legal `level` value.
PRODUCER = {
    "name": "Aleksandra Wolska",
    "email": f"vendor@{DEMO_EMAIL_DOMAIN}",
    "password": DEMO_PASSWORD,
    "company": "PartnerType Sp. z o.o.",
    "role": "admin",
    "level": "platinum",
    "phone": "+48 000 000 001",
    "bio": "Opiekun programu partnerskiego PartnerType.",
}

PARTNERS = [
    {
        "key": "bronze",
        "tier_name": "Bronze",
        "level": "bronze",
        "name": "Michał Sadowski",
        "email": f"partner.bronze@{DEMO_EMAIL_DOMAIN}",
        "company": "Nova Sklep sp. z o.o.",
        "phone": "+48 000 000 002",
        "bio": "Nowy partner, rynek e-commerce.",
    },
    {
        "key": "silver",
        "tier_name": "Silver",
        "level": "silver",
        "name": "Katarzyna Lis",
        "email": f"partner.silver@{DEMO_EMAIL_DOMAIN}",
        "company": "ProBiznes Konsulting sp. z o.o.",
        "phone": "+48 000 000 003",
        "bio": "Doradztwo biznesowe, klienci B2B.",
    },
    {
        "key": "gold",
        "tier_name": "Gold",
        "level": "gold",
        "name": "Tomasz Grabowski",
        "email": f"partner.gold@{DEMO_EMAIL_DOMAIN}",
        "company": "Digital Wave Kraków sp. z o.o.",
        "phone": "+48 000 000 004",
        "bio": "Agencja digital, wdrożenia SaaS.",
    },
    {
        "key": "platinum",
        "tier_name": "Platinum",
        "level": "platinum",
        "name": "Ewa Majewska",
        "email": f"partner.platinum@{DEMO_EMAIL_DOMAIN}",
        "company": "Grupa Inwestycyjna Baltis S.A.",
        "phone": "+48 000 000 005",
        "bio": "Partner strategiczny, 8 lat współpracy.",
    },
]

# One percent bonus and one cash bonus, each assigned to a different partner,
#kept off the
# duplicate pair's two partners so the duplicate commission comparison below
# stays driven only by tier rate and queue share.
PERCENT_BONUS_NAME = "Bonus za webinar"
PERCENT_BONUS_PARTNER_KEY = "bronze"
CASH_BONUS_NAME = "Nagroda kwartalna"
CASH_BONUS_PARTNER_KEY = "platinum"

# ─── Leads ────────────────────────────────────────────────────────────
# Eight distinct clients spread across the configured pipeline, plus
# one explicit duplicate resubmission of "Bluetec S.A." -- the
# twice-submitted-company pattern already used in the approved mockups. The
# duplicate's second submission deliberately varies NIP spacing and website
# case/scheme so the normalizers are visibly doing work, not just comparing
# identical strings.
#
# Each lead names a pipeline ROLE, not a literal stage key -- resolved at
# seed time against the producer's *active* stage list
# (`_resolve_stage_roles`), so a producer who renames/reorders stages before
# a demo still gets a fully populated board:
#   None        -- stays wherever a freshly created lead lands (the
#                  pipeline's own intake stage; api.leads.create_lead
#                  itself decides this, unrelated to this script)
#   "middle"    -- an in-between progress stage that is neither the intake
#                  stage, the request-for-information stage, nor the
#                  commission trigger (on the seeded six-stage pipeline this
#                  is the sole non-priority stage between intake and
#                  request-for-information)
#   "more_info" -- the stage the producer designated for requesting more
#                  information (structurally the last non-terminal stage
#                  immediately before the commission trigger)
#   "trigger"   -- the stage that computes commission
#   "win"       -- the terminal win outcome (walked through the trigger
#                  first, so its commission is genuinely computed, not
#                  fixture data)
#   "lose"      -- the terminal lose outcome
LEADS = [
    {
        "key": "bluetec_1",
        "partner_key": "silver",
        "client_name": "Robert Duda",
        "client_email": "robert.duda@bluetec.example",
        "client_phone": "+48 000 000 006",
        "company": "Bluetec S.A.",
        "value": 45000,
        "notes": "Zainteresowani wdrożeniem systemu ERP w dziale produkcji.",
        "sector": "Produkcja",
        "nip_raw": "0000001011",
        "website_raw": "bluetec.example",
        "product": "Wdrożenie ERP",
        "quantity": 1,
        "days_ago": 6,
        "role": "trigger",
    },
    {
        "key": "bluetec_2",
        "partner_key": "gold",
        "client_name": "Aneta Wilk",
        "client_email": "aneta.wilk@bluetec.example",
        "client_phone": "+48 000 000 007",
        "company": "Bluetec S.A.",
        "value": 45000,
        "notes": "Dział zakupów pyta o integrację z istniejącym systemem magazynowym.",
        "sector": "Produkcja",
        "nip_raw": "000-000-10-11",
        "website_raw": "https://WWW.BLUETEC.EXAMPLE/",
        "product": "Wdrożenie ERP",
        "quantity": 1,
        "days_ago": 3,
        "role": "trigger",
    },
    {
        "key": "fabryka_mebli",
        "partner_key": "bronze",
        "client_name": "Grzegorz Wiśniewski",
        "client_email": "g.wisniewski@fabrykamebli-wisniewski.example",
        "client_phone": "+48 000 000 008",
        "company": "Fabryka Mebli Wiśniewski sp.j.",
        "value": 62000,
        "notes": "Zamówienie na oprogramowanie do zarządzania produkcją mebli.",
        "sector": "Produkcja",
        "nip_raw": "0000001028",
        "website_raw": "fabrykamebli-wisniewski.example",
        "product": "System MES",
        "quantity": 3,
        "days_ago": 10,
        "role": "win",
    },
    {
        "key": "kowalski_logistyka",
        "partner_key": "silver",
        "client_name": "Marek Kowalski",
        "client_email": "m.kowalski@kowalski-logistyka.example",
        "client_phone": "+48 000 000 009",
        "company": "Kowalski Logistyka sp. z o.o.",
        "value": 18000,
        "notes": "Wstępne zapytanie o system śledzenia przesyłek.",
        "sector": "Logistyka",
        "nip_raw": "0000001034",
        "website_raw": "kowalski-logistyka.example",
        "product": "System TMS",
        "quantity": 1,
        "days_ago": 35,
        "role": None,
    },
    {
        "key": "zielona_energia",
        "partner_key": "gold",
        "client_name": "Paulina Wójcik",
        "client_email": "p.wojcik@zielonaenergia.example",
        "client_phone": "+48 000 000 010",
        "company": "Zielona Energia Polska S.A.",
        "value": 95000,
        "notes": "Rozważają panel do monitoringu instalacji fotowoltaicznych.",
        "sector": "Energetyka",
        "nip_raw": "0000001040",
        "website_raw": "zielonaenergia.example",
        "product": "Panel monitoringu OZE",
        "quantity": 1,
        "days_ago": 4,
        "role": "middle",
    },
    {
        "key": "studio_kreska",
        "partner_key": "platinum",
        "client_name": "Julia Nowicka",
        "client_email": "julia@studiokreska.example",
        "client_phone": "+48 000 000 011",
        "company": "Studio Graficzne Kreska",
        "value": 27000,
        "notes": "Potrzebują pakietu narzędzi do zarządzania projektami kreatywnymi.",
        "sector": "Marketing",
        "nip_raw": "0000001057",
        "website_raw": "studiokreska.example",
        "product": "Pakiet narzędzi projektowych",
        "quantity": 5,
        "days_ago": 8,
        "role": "more_info",
    },
    {
        "key": "handlex",
        "partner_key": "bronze",
        "client_name": "Adam Zawadzki",
        "client_email": "a.zawadzki@handlex.example",
        "client_phone": "+48 000 000 012",
        "company": "Handlex Dystrybucja sp. z o.o.",
        "value": 33000,
        "notes": "Klient wybrał ofertę konkurencji.",
        "sector": "Handel",
        "nip_raw": "0000001063",
        "website_raw": "handlex.example",
        "product": "System WMS",
        "quantity": 2,
        "days_ago": 14,
        "role": "lose",
    },
    {
        "key": "poltech_it",
        "partner_key": "silver",
        "client_name": "Bartosz Krajewski",
        "client_email": "b.krajewski@poltech-it.example",
        "client_phone": "+48 000 000 013",
        "company": "PolTech Rozwiązania IT sp. z o.o.",
        "value": 71000,
        "notes": "Zatwierdzony budżet na modernizację infrastruktury IT.",
        "sector": "IT",
        "nip_raw": "0000001086",
        "website_raw": "poltech-it.example",
        "product": "Modernizacja infrastruktury",
        "quantity": 1,
        "days_ago": 2,
        "role": "trigger",
    },
    {
        "key": "agrocentrum",
        "partner_key": "platinum",
        "client_name": "Natalia Sikora",
        "client_email": "n.sikora@agrocentrum-pomorze.example",
        "client_phone": "+48 000 000 014",
        "company": "Agrocentrum Pomorze sp. z o.o.",
        "value": 15000,
        "notes": "Zapytanie o system zarządzania magazynem zbożowym.",
        "sector": "Rolnictwo",
        "nip_raw": "0000001092",
        "website_raw": "agrocentrum-pomorze.example",
        "product": "System magazynowy",
        "quantity": 1,
        "days_ago": 1,
        "role": None,
    },
]

# Reserve budget for extra producer-added "middle" stages beyond the one the
# fixed narrative pool above already covers. Used
# only when the active pipeline has more than one such stage; each is a
# believable Polish company in its own right, not a filler placeholder.
# Upper bound on the demo board: a believable board has to show four
# registration decisions AND an occupant on each sales stage. Still small enough to read as one real week.
MAX_DEMO_LEADS = 12

SPARE_LEADS = [
    {
        "key": "ekomedia",
        "partner_key": "silver",
        "client_name": "Marek Zielinski",
        "client_email": "m.zielinski@ekomedia-poznan.example",
        "client_phone": "+48 000 000 015",
        "company": "EkoMedia Poznan sp. z o.o.",
        "value": 31000,
        "notes": "Pytaja o modul raportowania sprzedazy dla sieci punktow.",
        "sector": "Handel",
        "nip_raw": "0000001100",
        "website_raw": "ekomedia-poznan.example",
        "product": "System obsługi zamówień",
        "quantity": 1,
        "days_ago": 4,
        "role": "middle",
    },
    {
        "key": "metalprod",
        "partner_key": "bronze",
        "client_name": "Piotr Nowakowski",
        "client_email": "p.nowakowski@metalprod-gdynia.example",
        "client_phone": "+48 000 000 016",
        "company": "MetalProd Gdynia sp. z o.o.",
        "value": 52000,
        "notes": "Zapytanie o system planowania produkcji dla linii cięcia blach.",
        "sector": "Produkcja",
        "nip_raw": "0000001117",
        "website_raw": "metalprod-gdynia.example",
        "product": "System planowania produkcji",
        "quantity": 1,
        "days_ago": 7,
        "role": "middle",
    },
    {
        "key": "biurospec",
        "partner_key": "gold",
        "client_name": "Anna Sokołowska",
        "client_email": "a.sokolowska@biurospec.example",
        "client_phone": "+48 000 000 017",
        "company": "BiuroSpec Wyposażenie Biur S.A.",
        "value": 24000,
        "notes": "Rozważają system obsługi zamówień hurtowych.",
        "sector": "Handel",
        "nip_raw": "0000001123",
        "website_raw": "biurospec.example",
        "product": "System obsługi zamówień",
        "quantity": 1,
        "days_ago": 5,
        "role": "middle",
    },
]


def _resolve_stage_roles():
    """Return the active pipeline's roles, read from ``api.lead_stages``.

    Mirrors the structural derivation ``js/utils.js::Ui.buildStagePipeline``
    already established for the producer/partner UI, so this script and the
    screens it feeds agree on what each role means. Nothing here compares a
    stage against a literal word -- every role is read off ``kind`` and
    ``is_terminal``.

    Three of these roles are not stages at all: "intake", "more_info" and "lose" are values on the registration
    axis (the producer's decision), while "accepted", "middle" and "win"
    place a lead within the sales pipeline. Keeping them in one table is
    what lets the demo board show a lead in every meaningful state.
    """
    stages = [s for s in lead_stages.get_all() if s["active"]]
    non_terminal = [s for s in stages if not s["is_terminal"]]
    win = next((s for s in stages if s["is_terminal"] and s["kind"] == "win"), None)
    lose_stage = next((s for s in stages if s["is_terminal"] and s["kind"] == "lose"), None)
    first = non_terminal[0] if non_terminal else None

    return {
        "first": first,
        "win": win,
        "lose_stage": lose_stage,
        # Every non-terminal stage after the entry point is a place a partner
        # can park a live deal.
        "middle": non_terminal[1:],
    }


def _role_transitions(role, roles, middle_index):
    """Return the tuple of ``update_lead`` payloads that place a lead in the
    given ROLE against the currently active pipeline -- or ``None`` if the
    role has no available stage to target (only possible for a "middle" role
    beyond the pipeline's own count of extra stages).

    Each payload is a dict rather than a bare stage key, because a demo lead
    now has to be positioned on two axes.
    """
    accept = {"registration_status": "accepted"}
    # A refusal or a request for information owes the partner words, and that
    # rule lives in the domain now rather than at the HTTP boundary -- so the
    # seed states a reason like any other writer. This is a real message, not
    # a way around the requirement.
    DEMO_INFO_REASON = "Demo: prosimy o uzupełnienie danych kontaktowych klienta."
    DEMO_REJECT_REASON = "Demo: zgłoszenie poza obszarem działania producenta."
    if role is None:
        return ()
    if role == "intake":
        # Freshly submitted, awaiting the producer -- create_lead already
        # leaves it there, so there is nothing to apply.
        return ()
    if role == "more_info":
        return ({"registration_status": "needs_info",
                 "registration_message": DEMO_INFO_REASON},)
    if role == "lose":
        # The producer refused the registration. Distinct from a deal that
        # was accepted and then lost in the sale, which "win"'s sibling
        # stage covers.
        return ({"registration_status": "rejected",
                 "registration_message": DEMO_REJECT_REASON},)
    if role == "trigger":
        return (accept,)
    if role == "win":
        steps = [accept]
        if roles["win"]:
            steps.append({"status": roles["win"]["key"]})
        return tuple(steps)
    if role == "middle":
        stages = roles["middle"]
        if middle_index < len(stages):
            return (accept, {"status": stages[middle_index]["key"]})
        return None
    return ()


def _build_active_leads(roles):
    """Resolve the fixed narrative pool (plus spares if needed) against the
    active pipeline, returning (active_leads, skipped_stages).

    ``active_leads`` is every LEADS/SPARE_LEADS spec whose role resolved to
    a real stage, each carrying a ``transitions`` tuple of update payloads.
    ``skipped_stages`` lists any "middle" stage the 8-10 lead budget
    could not reach -- reported so a producer who heavily customises the
    pipeline sees exactly what the demo board will not show, rather than a
    silently incomplete board.
    """
    middle_used = 0
    active = []
    for spec in LEADS:
        transitions = _role_transitions(spec["role"], roles, middle_used if spec["role"] == "middle" else 0)
        if transitions is None:
            continue
        if spec["role"] == "middle":
            middle_used += 1
        active.append({**spec, "transitions": transitions})

    # The demo board is capped so it reads as a real week of work rather
    # than a stress test. The sales pipeline this seeds
    # has more "middle" stages than the narrative pool covers, so without
    # this ceiling every spare would activate and overshoot the range.
    for spec in SPARE_LEADS:
        if len(active) >= MAX_DEMO_LEADS:
            break
        transitions = _role_transitions("middle", roles, middle_used)
        if transitions is None:
            break
        middle_used += 1
        active.append({**spec, "transitions": transitions})

    skipped_stages = roles["middle"][middle_used:]
    return active, skipped_stages


def _teardown():
    """Delete only the rows this script owns, in FK-safe order.

    Ownership is determined solely by ``DEMO_EMAIL_DOMAIN`` -- a row this
    script did not create is never touched. ``leads.partner_id``
    has no cascade, so leads must go before their partner; ``audit_log`` has
    no foreign key on ``entity``/``entity_id`` at all, so lead- and
    partner-scoped entries left by a *previous* run of this same script are
    deleted explicitly or they would accumulate forever across resets.
    """
    conn = get_conn()
    try:
        email_pattern = f"%@{DEMO_EMAIL_DOMAIN}"
        seeded_user_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM users WHERE email LIKE ?", (email_pattern,)
            ).fetchall()
        ]

        seeded_lead_ids = []
        if seeded_user_ids:
            placeholders = ",".join("?" for _ in seeded_user_ids)
            seeded_lead_ids = [
                row["id"]
                for row in conn.execute(
                    f"SELECT id FROM leads WHERE partner_id IN ({placeholders})",
                    seeded_user_ids,
                ).fetchall()
            ]

        if seeded_lead_ids:
            lead_placeholders = ",".join("?" for _ in seeded_lead_ids)
            conn.execute(
                f"DELETE FROM audit_log WHERE entity='lead' AND entity_id IN ({lead_placeholders})",
                [str(lead_id) for lead_id in seeded_lead_ids],
            )
            conn.execute(
                f"DELETE FROM leads WHERE id IN ({lead_placeholders})", seeded_lead_ids
            )

        if seeded_user_ids:
            user_placeholders = ",".join("?" for _ in seeded_user_ids)
            # Partner-scoped audit entries (tier/bonus changes this script's
            # own previous run made) reference a user id that is about to be
            # deleted too; clean them up the same way as the lead entries
            # above so a repeated reset does not grow audit_log forever.
            conn.execute(
                f"DELETE FROM audit_log WHERE entity='partner' AND entity_id IN ({user_placeholders})",
                [str(user_id) for user_id in seeded_user_ids],
            )
            # sessions, notifications, lead_comments and
            # partner_bonus_assignments all cascade on users(id).
            conn.execute(f"DELETE FROM users WHERE id IN ({user_placeholders})", seeded_user_ids)

        conn.commit()
        return len(seeded_user_ids), len(seeded_lead_ids)
    finally:
        conn.close()


# A fresh database has no commission tiers: the producer defines their own.
# The demo partners need these, so any that are missing are created through
# the real tier path; tiers the producer already has are left as they are.
DEMO_TIERS = [
    {"name": "Bronze", "base_rate": 5.0, "color": "#cd7f32", "sort_order": 0},
    {"name": "Silver", "base_rate": 6.0, "color": "#a8a9ad", "sort_order": 1},
    {"name": "Gold", "base_rate": 7.0, "color": "#d4af37", "sort_order": 2},
    {"name": "Platinum", "base_rate": 10.0, "color": "#6246ea", "sort_order": 3},
]


def _ensure_demo_tiers(producer):
    """Return {tier name: id}, creating the demo tiers that do not exist yet."""
    existing = {tier["name"]: tier["id"] for tier in commission_tiers.get_all()}
    for spec in DEMO_TIERS:
        if spec["name"] not in existing:
            created = commission_tiers.create_tier(dict(spec), producer)
            existing[created["name"]] = created["id"]
    return existing


def _create_accounts():
    """Create the producer and every partner through the real user path."""
    producer = create_user(dict(PRODUCER))

    tier_ids = _ensure_demo_tiers(producer)

    partners = {}
    for spec in PARTNERS:
        user = create_user(
            {
                "name": spec["name"],
                "email": spec["email"],
                "password": DEMO_PASSWORD,
                "company": spec["company"],
                "role": "partner",
                "level": spec["level"],
                "phone": spec["phone"],
                "bio": spec["bio"],
            }
        )
        # Set tier_id through the real tier helper so legacy `level` and
        # `tier_id` describe the same standing on every screen.
        partner_bonuses.update_tier(user["id"], tier_ids[spec["tier_name"]], producer)
        partners[spec["key"]] = user

    return producer, partners


def _ensure_bonus(producer, name, btype, value, description, name_en):
    """Return the id of the named bonus, creating it if the database has none."""
    for bonus in bonus_defs.get_all():
        if bonus["name"] == name:
            return bonus["id"]
    created = bonus_defs.create_bonus(
        {"name": name, "name_en": name_en, "type": btype,
         "default_value": value, "description": description},
        producer,
    )
    return created["id"]


def _assign_bonuses(producer, partners):
    """One assigned percent bonus, one granted cash bonus.

    These are two different mechanisms, not one with a type
    flag: a percent bonus is assigned and changes the rate, a cash bonus is
    granted and goes to the payout queue (api/bonus_awards.py). The demo has to
    exercise both, or the Commissions screens have nothing to show.
    """
    percent_bonus_id = _ensure_bonus(
        producer, PERCENT_BONUS_NAME, "percent", 2.0,
        "Prowadzenie lub udział w webinarze", "Webinar bonus",
    )
    cash_bonus_id = _ensure_bonus(
        producer, CASH_BONUS_NAME, "cash", 500.0,
        "Gotówkowa nagroda za wyniki kwartalne", "Quarterly award",
    )

    partner_bonuses.assign_bonus(
        partners[PERCENT_BONUS_PARTNER_KEY]["id"], percent_bonus_id, None, producer
    )
    bonus_awards.grant(
        [partners[CASH_BONUS_PARTNER_KEY]["id"]], cash_bonus_id, producer
    )


def _restore_queue_settings(producer):
    """Restore the approved duplicate-queue configuration."""
    settings_api.set_many(
        {
            "lead_duplicate_policy": DUPLICATE_POLICY,
            "lead_queue_shares": QUEUE_SHARES,
            "lead_queue_max": QUEUE_MAX,
        },
        producer,
    )


def _create_leads(partners, active_leads):
    """Create every lead via ``create_lead`` as the owning partner.

    Routing through the real creation path is what makes duplicate
    detection, normalization and queue-position assignment match exactly
    what the running application would produce for the same submissions.
    """
    lead_ids = {}
    for spec in active_leads:
        partner = partners[spec["partner_key"]]
        result = create_lead(
            partner,
            {
                "client_name": spec["client_name"],
                "client_email": spec["client_email"],
                "client_phone": spec["client_phone"],
                "company": spec["company"],
                "value": spec["value"],
                "notes": spec["notes"],
                "sector": spec["sector"],
                "nip": spec["nip_raw"],
                "website": spec["website_raw"],
                "product": spec["product"],
                "quantity": spec["quantity"],
            },
        )
        lead_ids[spec["key"]] = result["id"]
    return lead_ids


def _backdate_leads(lead_ids, active_leads):
    """Spread submission dates so age/deadline columns show a real range."""
    conn = get_conn()
    try:
        now = datetime.utcnow()
        for spec in active_leads:
            days_ago = spec.get("days_ago")
            if not days_ago:
                continue
            timestamp = (now - timedelta(days=days_ago)).isoformat()
            conn.execute(
                "UPDATE leads SET created_at=?, updated_at=? WHERE id=?",
                (timestamp, timestamp, lead_ids[spec["key"]]),
            )
        conn.commit()
    finally:
        conn.close()


def _apply_transitions(producer, lead_ids, active_leads):
    """Move each lead through its target decision(s) as the producer.

    Routing through ``update_lead`` (rather than writing ``status``/
    ``commission`` directly) is what proves the accepted-lead commission
    the backend computes from tier rate + active percent
    bonuses + queue share is actually correct, not merely fixture data.
    Each ``transitions`` entry is already a real update payload, resolved by
    ``_build_active_leads`` against the active pipeline -- never a literal
    status word.
    """
    for spec in active_leads:
        for payload in spec["transitions"]:
            update_lead(lead_ids[spec["key"]], producer, payload)


def _print_credentials(partners):
    print()
    print("═" * 62)
    print("  Demo sign-in details (one password for every account):")
    print(f"    password: {DEMO_PASSWORD}")
    print()
    print(f"  Vendor (admin): {PRODUCER['email']}")
    print("  Partners:")
    for spec in PARTNERS:
        partner = partners[spec["key"]]
        print(f"    {spec['tier_name']:<9}— {partner['email']}  ({spec['company']})")
    print("═" * 62)


@system_context("developer: demo seed")
def main():
    if os.environ.get('PARTNERTYPE_ENV') not in ('development', 'test'):
        raise RuntimeError('Demo seed allowed only in development/test')
    db.check_schema()

    removed_users, removed_leads = _teardown()
    print(f"  ↳ removed the previous demo set: {removed_users} accounts, {removed_leads} leads")

    producer, partners = _create_accounts()
    _assign_bonuses(producer, partners)
    _restore_queue_settings(producer)

    roles = _resolve_stage_roles()
    active_leads, skipped_stages = _build_active_leads(roles)

    lead_ids = _create_leads(partners, active_leads)
    _backdate_leads(lead_ids, active_leads)
    _apply_transitions(producer, lead_ids, active_leads)

    print(
        f"  ↳ created a new demo set: {1 + len(partners)} accounts "
        f"(1 vendor + {len(partners)} partners), {len(lead_ids)} leads"
    )
    if skipped_stages:
        names = ", ".join(stage["name"] for stage in skipped_stages)
        print(
            f"  ↳ NOTE: {len(skipped_stages)} stage(s) without a sample lead "
            f"(outside the budget of {len(LEADS)}-{len(LEADS) + len(SPARE_LEADS)} leads): {names}"
        )

    _print_credentials(partners)


if __name__ == "__main__":
    main()
