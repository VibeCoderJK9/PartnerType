"""
PartnerType — Blocks: the way one producer fits the shared core to its own process.

Every company runs its partner channel differently, so the
product is a shared core plus small blocks a producer switches on. A block is
written by the producer's own developer, by the producer's AI agent, or by us
on order; shared blocks end up in a common catalogue.

What a block may do, and nothing more:

  * react to a lead event after it is committed (`events` + `on_event`), and/or
  * run on a schedule -- the daily maintenance command, or the admin's
    "Check now" button (`run`).

Rules every block follows:

  * Off by default. Only a producer's configuring user turns it on.
  * It acts through the domain modules (api/notifications.py, api/leads.py,
    ...), never through its own SQL on CRM tables. Its own memory lives in
    `block_state` through `get_state` / `set_state`.
  * Events carry ids only. A block that needs a name reads it itself, under
    the same rules as any other code in this instance.
  * A failing block never breaks the write that triggered it and never logs
    exception text (it may contain client data) -- only its type.

How to write one: copy `api/blocks/inactive_partner.py`, give it a new id,
and add it to `_BUILTIN` at the bottom of this file. See
`docs/BLOCKS.md` for the walkthrough meant for people and their AI agents.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from api import audit
from api.authz import can_configure
from api.db import get_conn
from api.postgres import system_context
from api.validation import ValidationError

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,47}$")


@dataclass(frozen=True)
class ConfigField:
    """One number the producer can tune, e.g. "after how many days"."""
    name: str
    label_key: str
    default: int
    minimum: int
    maximum: int


@dataclass(frozen=True)
class Block:
    id: str
    name_key: str
    description_key: str
    events: tuple = ()
    config_fields: tuple = ()
    on_event: Optional[Callable] = None   # on_event(event_type, lead_id, actor_id, config)
    run: Optional[Callable] = None        # run(config, now) -> dict summary
    meta: dict = field(default_factory=dict)


REGISTRY: dict = {}


def register(block: Block) -> Block:
    if not _ID_RE.fullmatch(block.id):
        raise ValueError(f"Invalid block id: {block.id!r}")
    if block.id in REGISTRY:
        raise ValueError(f"Block already registered: {block.id}")
    REGISTRY[block.id] = block
    return block


# ─── STATE ──────────────────────────────────────────────────────────────────

def get_state(conn, block_id: str, subject: str) -> dict:
    row = conn.execute(
        "SELECT value_json FROM block_state WHERE block_id = ? AND subject = ?",
        (block_id, subject),
    ).fetchone()
    if row is None:
        return {}
    try:
        value = json.loads(row["value_json"])
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def set_state(conn, block_id: str, subject: str, value: dict) -> None:
    conn.execute(
        """INSERT INTO block_state (block_id, subject, value_json, updated_at)
           VALUES (?, ?, ?, now())
           ON CONFLICT (block_id, subject)
           DO UPDATE SET value_json = EXCLUDED.value_json, updated_at = now()""",
        (block_id, subject, json.dumps(value, separators=(",", ":"))),
    )


# ─── CONFIGURATION ──────────────────────────────────────────────────────────

def _defaults(block: Block) -> dict:
    return {f.name: f.default for f in block.config_fields}


def _stored(conn) -> dict:
    rows = conn.execute("SELECT block_id, enabled, config_json FROM block_config").fetchall()
    stored = {}
    for row in rows:
        try:
            config = json.loads(row["config_json"])
        except (TypeError, ValueError):
            config = {}
        stored[row["block_id"]] = (bool(row["enabled"]), config if isinstance(config, dict) else {})
    return stored


def _effective(block: Block, stored_entry) -> tuple:
    enabled, saved = stored_entry if stored_entry else (False, {})
    config = _defaults(block)
    for f in block.config_fields:
        value = saved.get(f.name)
        if isinstance(value, int) and not isinstance(value, bool) and f.minimum <= value <= f.maximum:
            config[f.name] = value
    return enabled, config


def _describe(block: Block, enabled: bool, config: dict) -> dict:
    return {
        "id": block.id,
        "name_key": block.name_key,
        "description_key": block.description_key,
        "enabled": enabled,
        "config": config,
        "config_fields": [
            {"name": f.name, "label_key": f.label_key, "default": f.default,
             "min": f.minimum, "max": f.maximum}
            for f in block.config_fields
        ],
        "events": list(block.events),
        "scheduled": block.run is not None,
    }


def list_blocks() -> list:
    conn = get_conn()
    try:
        stored = _stored(conn)
    finally:
        conn.close()
    return [_describe(b, *_effective(b, stored.get(b.id))) for b in REGISTRY.values()]


def _validated_update(block: Block, data) -> tuple:
    if not isinstance(data, dict):
        raise ValidationError(t("Invalid block data."))
    unknown = set(data) - {"enabled", "config"}
    if unknown:
        raise ValidationError(t("Unknown fields: {fields}", fields=", ".join(sorted(unknown))))
    enabled = data.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise ValidationError(t("Field 'enabled' must be true or false."))
    config = data.get("config")
    if config is not None and not isinstance(config, dict):
        raise ValidationError(t("Block settings must be an object."))
    fields = {f.name: f for f in block.config_fields}
    for name, value in (config or {}).items():
        f = fields.get(name)
        if f is None:
            raise ValidationError(t("Unknown block setting: {name}", name=name))
        if not isinstance(value, int) or isinstance(value, bool) or not f.minimum <= value <= f.maximum:
            raise ValidationError(t("The value must be a number from {minimum} to {maximum}.", minimum=f.minimum, maximum=f.maximum))
    return enabled, config


def update_block(block_id: str, data, user: dict) -> dict:
    block = REGISTRY.get(block_id)
    if block is None:
        raise LookupError(block_id)
    enabled, config_update = _validated_update(block, data)
    conn = get_conn()
    try:
        current_enabled, current_config = _effective(block, _stored(conn).get(block.id))
        new_enabled = current_enabled if enabled is None else enabled
        new_config = dict(current_config)
        new_config.update(config_update or {})
        conn.execute(
            """INSERT INTO block_config (block_id, enabled, config_json, updated_at)
               VALUES (?, ?, ?, now())
               ON CONFLICT (block_id)
               DO UPDATE SET enabled = EXCLUDED.enabled,
                             config_json = EXCLUDED.config_json,
                             updated_at = now()""",
            (block.id, 1 if new_enabled else 0, json.dumps(new_config, separators=(",", ":"))),
        )
        conn.commit()
    finally:
        conn.close()
    audit.log(user, "block.update", "block", block.id,
              f"{block.id}: {'on' if new_enabled else 'off'} {json.dumps(new_config, separators=(',', ':'))}")
    return _describe(block, new_enabled, new_config)


def _enabled_blocks() -> list:
    conn = get_conn()
    try:
        stored = _stored(conn)
    finally:
        conn.close()
    active = []
    for block in REGISTRY.values():
        enabled, config = _effective(block, stored.get(block.id))
        if enabled:
            active.append((block, config))
    return active


# ─── RUNNING ────────────────────────────────────────────────────────────────

def _warn(block_id, where, exc):
    # Never the message: it may carry client data.
    logger.warning("Block failed: block=%s where=%s type=%s", block_id, where, type(exc).__name__)


def emit(event_type: str, lead_id: str, actor_id) -> None:
    """Hand one committed lead event to every enabled block listening for it.

    Called after commit. Never raises: a block is an add-on, and the write
    that triggered it has already been saved and confirmed to the user.
    """
    try:
        listeners = [(b, c) for b, c in _enabled_blocks()
                     if b.on_event is not None and event_type in b.events]
    except Exception as exc:
        _warn("*", "emit", exc)
        return
    for block, config in listeners:
        try:
            # A block acts for the producer, not for whoever triggered the
            # event (a partner's lead may remind the producer about others).
            with system_context(f"block {block.id} event"):
                block.on_event(event_type, lead_id, actor_id, config)
        except Exception as exc:
            _warn(block.id, event_type, exc)


def run_scheduled(block_id: Optional[str] = None, now: Optional[datetime] = None) -> dict:
    """Run the scheduled part of enabled blocks; return what each one did."""
    now = now or datetime.utcnow()
    results = {}
    for block, config in _enabled_blocks():
        if block.run is None or (block_id and block.id != block_id):
            continue
        try:
            with system_context(f"block {block.id} scheduled run"):
                results[block.id] = block.run(config, now)
        except Exception as exc:
            _warn(block.id, "run", exc)
            results[block.id] = {"error": type(exc).__name__}
    return results


# ─── HTTP ───────────────────────────────────────────────────────────────────

def handle_list(handler, user, qs):
    if not can_configure(user):
        return 403, {"error": t("Access denied.")}
    return 200, {"blocks": list_blocks()}


def handle_update(handler, user, block_id, body):
    if not can_configure(user):
        return 403, {"error": t("Access denied.")}
    try:
        return 200, {"block": update_block(block_id, body, user)}
    except LookupError:
        return 404, {"error": t("There is no such block.")}


def handle_run(handler, user, block_id):
    if not can_configure(user):
        return 403, {"error": t("Access denied.")}
    block = REGISTRY.get(block_id)
    if block is None:
        return 404, {"error": t("There is no such block.")}
    if block.run is None:
        return 400, {"error": t("This block has no task to run.")}
    result = run_scheduled(block_id)
    if block_id not in result:
        return 409, {"error": t("The block is switched off. Switch it on to run it.")}
    audit.log(user, "block.run", "block", block_id, json.dumps(result[block_id], separators=(",", ":")))
    return 200, {"result": result[block_id]}


# ─── BUILT-IN BLOCKS ────────────────────────────────────────────────────────

from api.blocks import inactive_partner  # noqa: E402
from api.i18n import t

_BUILTIN = (inactive_partner.BLOCK,)
for _block in _BUILTIN:
    register(_block)
