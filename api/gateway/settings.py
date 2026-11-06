"""Integration switches, exposed field groups and limits -- read side only.

Stored in the existing `settings` key/value table under the `integrations_`
prefix, but deliberately NOT listed in
`api/settings_api.DEFAULTS`/`EDITABLE_KEYS`: those keys are writable by every
`can_configure` role, i.e. the moderator too, and only the administrator switches the external door.
The one writer
is `set_integration_settings` below, guarded by
`authz.can_manage_integrations`; `api/settings_api.get_all` hides the rows
from the general Settings read.

Every reader fails closed: a missing, empty or unparsable value means "off" or
"nothing exposed", never "on". Operator caps come from the instance's
environment, and an invalid cap is a configuration error that raises rather
than silently falling back to a default nobody chose.
"""
import json
import os
from datetime import datetime

from api import audit, authz
from api.db import get_conn
from api.http_security import normalize_origin
from api.validation import ValidationError
from api.i18n import N_, t

KEY_ENABLED = "integrations_enabled"
KEY_PARTNERS = "integrations_partners_enabled"
KEY_FIELD_GROUPS = "integrations_field_groups"
KEY_RATE_MINUTE = "integrations_rate_per_minute"
KEY_RATE_DAY = "integrations_rate_per_day"

# (env var, default, minimum, maximum) -- the operator's ceiling per instance.
_CAPS = {
    "rate_per_minute": ("PARTNERTYPE_API_RATE_PER_MINUTE", 60, 1, 600),
    "rate_per_day": ("PARTNERTYPE_API_RATE_PER_DAY", 5000, 1, 100000),
    "register_per_hour": ("PARTNERTYPE_OAUTH_REGISTER_PER_HOUR", 20, 1, 1000),
}

_DEFAULT_EXTRA_ORIGINS = "https://claude.ai,https://claude.com"
_DEFAULT_REDIRECT_HOSTS = "claude.ai,claude.com"


def _read(key):
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return None if row is None else row["value"]
    finally:
        conn.close()


def _flag(key) -> bool:
    return str(_read(key) or "").strip() == "1"


def integrations_enabled() -> bool:
    """The administrator's master switch. Off unless exactly "1"."""
    return _flag(KEY_ENABLED)


def partners_enabled() -> bool:
    """Whether partners may connect their own AI. Off unless "1"."""
    return _flag(KEY_PARTNERS)


def enabled_field_groups() -> frozenset:
    """The switchable field groups the administrator exposes.

    The stored value is a JSON list of group names. Anything else -- missing,
    malformed, not a list, unknown names -- contributes nothing, so a broken
    row can only ever hide data, never reveal it. `base` is always on and is
    not part of this set.
    """
    from api.gateway.fields import SWITCHABLE_GROUPS

    raw = _read(KEY_FIELD_GROUPS)
    if not raw:
        return frozenset()
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return frozenset()
    if not isinstance(value, list):
        return frozenset()
    return frozenset(v for v in value if isinstance(v, str)) & SWITCHABLE_GROUPS


def _cap(name):
    env, default, low, high = _CAPS[name]
    raw = os.environ.get(env, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{env} must be an integer {low}..{high}") from None
    if not low <= value <= high:
        raise ValueError(f"{env} must be an integer {low}..{high}")
    return value


def operator_caps() -> dict:
    """The operator's limits for this instance, from the environment."""
    return {name: _cap(name) for name in _CAPS}


def _stored_limit(key, cap):
    raw = _read(key)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return cap
    return value if value >= 1 else cap


def effective_limits() -> dict:
    """Per-connection limits in force: the admin's value, never above the cap."""
    caps = operator_caps()
    minute_cap, day_cap = caps["rate_per_minute"], caps["rate_per_day"]
    return {
        "per_minute": min(_stored_limit(KEY_RATE_MINUTE, minute_cap), minute_cap),
        "per_day": min(_stored_limit(KEY_RATE_DAY, day_cap), day_cap),
    }


def oauth_redirect_hosts():
    """Hosts an OAuth client may send authorization codes to over https.

    Default: Claude's web hosts. Loopback (Claude Code) is always allowed and
    is not listed here. The operator widens the list with
    PARTNERTYPE_OAUTH_REDIRECT_HOSTS (comma-separated hostnames); "*" accepts
    any https host. A short list keeps a stranger from registering a look-alike
    connector and phishing a user into granting it read access.
    """
    raw = os.environ.get("PARTNERTYPE_OAUTH_REDIRECT_HOSTS", _DEFAULT_REDIRECT_HOSTS)
    hosts = {h.strip().lower().rstrip(".") for h in raw.split(",") if h.strip()}
    return None if "*" in hosts else frozenset(hosts)


def mcp_allowed_origins(base_url) -> tuple:
    """Origins a browser-borne request to /mcp may carry.

    The instance itself plus the operator's list (default: Claude's web
    origins). Every entry goes through the same normaliser as the app's CORS
    policy; anything it rejects is dropped rather than trusted.
    """
    extra = os.environ.get("PARTNERTYPE_MCP_EXTRA_ORIGINS", _DEFAULT_EXTRA_ORIGINS)
    result = []
    for value in [base_url, *extra.split(",")]:
        origin = normalize_origin((value or "").strip())
        if origin and origin not in result:
            result.append(origin)
    return tuple(result)


# ─── writer ─────────────────────────────────────────────────────

WRITABLE_FIELDS = frozenset({
    "enabled", "partners_enabled", "field_groups", "rate_per_minute",
    "rate_per_day", "acknowledge_eu_transfer",
})

_RATE_LABELS = {
    "rate_per_minute": N_("Limit per minute"),
    "rate_per_day": N_("Limit per day"),
}


def _bool_field(updates, name):
    value = updates[name]
    # JSON booleans only: "1", 1 or "true" are not a decision anybody made.
    if not isinstance(value, bool):
        raise ValidationError(t("The switch must be true or false."))
    return value


def _field_groups_field(value):
    from api.gateway.fields import SWITCHABLE_GROUPS

    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ValidationError(t("Field groups: give a list of group names."))
    if len(set(value)) != len(value):
        raise ValidationError(t("Field groups cannot repeat."))
    if any(v not in SWITCHABLE_GROUPS for v in value):
        # The unknown name is not echoed: messages here never quote the request.
        raise ValidationError(t("Unknown field group."))
    return sorted(value)


def _rate_field(name, value, cap):
    label = t(_RATE_LABELS[name])
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError(t("{label}: give a positive integer.", label=label))
    if value > cap:
        raise ValidationError(
            t("The operator's limit is {cap}, so the {label} cannot be higher.",
              cap=cap, label=label.lower()))
    return value


def _stored(conn, key):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]


def _stored_groups(raw):
    try:
        value = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return sorted(v for v in value if isinstance(v, str)) if isinstance(value, list) else []


def _stored_int(raw):
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _upsert(conn, key, value):
    conn.execute(
        """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value,
                                           updated_at = EXCLUDED.updated_at""",
        (key, value, datetime.utcnow().isoformat()),
    )


def set_integration_settings(user, updates) -> dict:
    """Change the integration switches. Admin only.

    Accepts any subset of WRITABLE_FIELDS with strict JSON types. Turning
    integrations ON requires `acknowledge_eu_transfer: true` in the same
    request -- the RODO warning is acknowledged on the server, not only in a
    browser checkbox. Turning them OFF needs nothing and deletes no
    key or grant: the gateway refuses every call while the switch is off.
    Limits above the operator's caps are refused rather than clamped.

    Only values that actually changed are written to the audit log, after
    the commit, each with its new value as the detail.
    Returns the stored state: {enabled, partners_enabled, field_groups,
    rate_per_minute, rate_per_day} (rates None when never set).
    """
    if not authz.can_manage_integrations(user):
        raise PermissionError(t("Only an administrator manages integrations."))
    if not isinstance(updates, dict):
        raise ValidationError(t("Invalid integration settings."))
    if set(updates) - WRITABLE_FIELDS:
        raise ValidationError(t("Unknown integration setting."))

    parsed = {}
    for name in ("enabled", "partners_enabled", "acknowledge_eu_transfer"):
        if name in updates:
            parsed[name] = _bool_field(updates, name)
    if "field_groups" in updates:
        parsed["field_groups"] = _field_groups_field(updates["field_groups"])
    caps = operator_caps()
    for name in ("rate_per_minute", "rate_per_day"):
        if name in updates:
            parsed[name] = _rate_field(name, updates[name], caps[name])

    changes = []
    with get_conn() as conn:
        was_enabled = str(_stored(conn, KEY_ENABLED) or "").strip() == "1"
        was_partners = str(_stored(conn, KEY_PARTNERS) or "").strip() == "1"
        old_groups = _stored_groups(_stored(conn, KEY_FIELD_GROUPS))
        old_rates = {
            "rate_per_minute": _stored_int(_stored(conn, KEY_RATE_MINUTE)),
            "rate_per_day": _stored_int(_stored(conn, KEY_RATE_DAY)),
        }

        if "enabled" in parsed:
            enabled = parsed["enabled"]
            if enabled and not was_enabled and parsed.get("acknowledge_eu_transfer") is not True:
                raise ValidationError(
                    t("To turn integrations on, confirm that data may be processed outside "
                      "the European Union."))
            _upsert(conn, KEY_ENABLED, "1" if enabled else "0")
            if enabled != was_enabled:
                changes.append(("enabled", enabled))
        if "partners_enabled" in parsed:
            partners = parsed["partners_enabled"]
            _upsert(conn, KEY_PARTNERS, "1" if partners else "0")
            if partners != was_partners:
                changes.append(("partners_enabled", partners))
        if "field_groups" in parsed:
            groups = parsed["field_groups"]
            _upsert(conn, KEY_FIELD_GROUPS, json.dumps(groups))
            if groups != old_groups:
                changes.append(("field_groups", groups))
        for name, key in (("rate_per_minute", KEY_RATE_MINUTE), ("rate_per_day", KEY_RATE_DAY)):
            if name in parsed:
                _upsert(conn, key, str(parsed[name]))
                if parsed[name] != old_rates[name]:
                    changes.append((name, parsed[name]))

    for name, value in changes:
        _audit_change(user, name, value)
    return stored_state()


def _audit_change(user, name, value):
    # One literal action per call site: js/__tests__/audit-labels.test.js reads
    # them from the source, and an action must never be computed.
    if name == "enabled" and value:
        audit.log(user, "integration.enabled", "settings", KEY_ENABLED, "1")
    elif name == "enabled":
        audit.log(user, "integration.disabled", "settings", KEY_ENABLED, "0")
    elif name == "partners_enabled" and value:
        audit.log(user, "integration.partners_enabled", "settings", KEY_PARTNERS, "1")
    elif name == "partners_enabled":
        audit.log(user, "integration.partners_disabled", "settings", KEY_PARTNERS, "0")
    elif name == "field_groups":
        audit.log(user, "integration.fields_changed", "settings", KEY_FIELD_GROUPS,
                  ", ".join(value) or "—")
    elif name == "rate_per_minute":
        audit.log(user, "integration.limits_changed", "settings", KEY_RATE_MINUTE, str(value))
    elif name == "rate_per_day":
        audit.log(user, "integration.limits_changed", "settings", KEY_RATE_DAY, str(value))


def stored_state() -> dict:
    """The switches as stored, for the writer's return value."""
    return {
        "enabled": integrations_enabled(),
        "partners_enabled": partners_enabled(),
        "field_groups": sorted(enabled_field_groups()),
        "rate_per_minute": _stored_int(_read(KEY_RATE_MINUTE)),
        "rate_per_day": _stored_int(_read(KEY_RATE_DAY)),
    }


def snapshot(base_url) -> dict:
    """Everything the Integrations screen shows."""
    from api.gateway.auth import resource_url
    from api.gateway.fields import FIELD_GROUPS

    base = (base_url or "").rstrip("/")
    caps = operator_caps()
    return {
        "enabled": integrations_enabled(),
        "partners_enabled": partners_enabled(),
        "field_groups": sorted(enabled_field_groups()),
        "field_group_catalogue": [
            {"key": key, "fields": list(names)} for key, names in FIELD_GROUPS.items()
        ],
        "limits": effective_limits(),
        "operator_caps": caps,
        "mcp_url": resource_url(base, "mcp"),
        "api_base": resource_url(base, "api_v1"),
        "openapi_url": resource_url(base, "api_v1") + "/openapi.json",
    }
