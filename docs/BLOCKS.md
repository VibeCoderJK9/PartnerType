# Blocks: fitting PartnerType to your process

Every company runs its partners differently. Instead of changing the core, you add a **block**:
a small function that reacts to an event or runs on a schedule. This file is written so that a
person and their AI agent (for example Claude Code) can read it before building a new block.

## What a block can do

1. **React to a lead event**, after the change is saved in the database:
   `lead.created`, `lead.assign`, `registration.accept`, `registration.reject`, `stage.move`,
   `details.update`, `lead.reopen`, `information.response` (full list: `event_type` in the
   `lead_workflow_events` table), and `affiliate.conversion` when an approved shop order becomes
   a won lead (see `docs/AFFILIATE.md`).
2. **Run on a schedule**: daily together with the `maintenance` command, or when someone clicks
   "Check now" in Settings › Blocks.

## Rules (mandatory)

- **Off by default.** The producer's administrator switches it on in Settings › Blocks.
- **Works through the domain modules**, for example `api/notifications.py` or the functions in
  `api/leads.py`. It never writes its own SQL to CRM tables. It may read, it may not write.
- **Keeps its memory in `block_state`** through `get_state` / `set_state`. It stores only
  identifiers and dates, no names and no client data.
- **An event carries identifiers only** (`event_type`, `lead_id`, `actor_id`). If the block needs
  data, it reads it itself.
- **A block error never breaks the write** that triggered it. The core catches the exception and
  logs only its type, never its message (the message may contain client data).
- **Sends no data outside the installation.** A block that would connect to an outside service
  needs a separate decision (GDPR: the block's author becomes a data processor). There is no such
  path yet.

## Building a block in 4 steps

1. Copy `api/blocks/inactive_partner.py` to `api/blocks/<your_name>.py`.
2. Change `BLOCK_ID` and the logic in `run(config, now)`, or add
   `on_event(event_type, lead_id, actor_id, config)` and an `events=(...)` list.
3. Add the module to `_BUILTIN` at the bottom of `api/blocks/__init__.py`.
4. Add the texts to `locales/en.json` and `locales/pl.json`: `name_key`,
   `description_key`, the `label_key` of every setting and the notification keys. Add tests in
   `api/tests/unit/`.

### Skeleton

```python
from api.blocks import Block, ConfigField, get_state, set_state
from api import notifications as notif

BLOCK_ID = "my_block"

def on_event(event_type, lead_id, actor_id, config):
    ...  # e.g. notify the channel manager when a lead reaches stage X

def run(config, now):
    ...  # e.g. find leads with no movement for config["days"] days
    return {"checked": 0, "reminded": 0}

BLOCK = Block(
    id=BLOCK_ID,
    name_key="block_my_name",
    description_key="block_my_desc",
    events=("stage.move",),
    config_fields=(ConfigField(name="days", label_key="block_my_days",
                               default=7, minimum=1, maximum=90),),
    on_event=on_event,
    run=run,
)
```

## Prompt for an AI agent (copy and paste)

> Read `docs/BLOCKS.md` and `api/blocks/inactive_partner.py`. Build a block `<id>` that
> `<one-sentence description, e.g. "reminds the channel manager when a lead stays in the Offer stage longer than N days">`.
> Follow the rules in the file: off by default, only identifiers in `block_state`, writes through
> the domain modules, PL and EN texts in `locales/*.json`, tests in `api/tests/unit/`. Run the tests.

## First block: inactive partner reminder

`api/blocks/inactive_partner.py`. If an active partner has not submitted a lead for `days` days
(default 30, from 7 to 180), the administrators get one in-app notification. The next one comes only
if the partner stays silent for another `days` days. It works without AI, and no data leaves the
installation.

## What is next

A permission manifest for each block (events, data scope, allowed domains), isolated execution, a
catalogue of blocks shared by users, and a "Runs inside PartnerType" badge.
