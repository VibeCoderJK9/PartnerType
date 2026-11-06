# Affiliate programme

Partners share a personal link to your shop. When a visitor who came through that link pays, the
shop tells PartnerType with a signed webhook, and the partner's commission appears automatically:
it uses the same commission rules as any won lead, and it waits for your approval.

```text
partner's link ──► /r/<code> ──► your shop (?pt_ref=…&pt_click=…)
                                   │  snippet stores the referral after consent
                                   ▼
                          visitor pays ──► signed webhook ──► PENDING order
                                                                │ you approve (or auto-approve)
                                                                ▼
                                            won lead of the partner ──► Payouts
```

The programme is **off by default**. Nothing is reachable until an administrator switches it on.

## Set it up

1. **Affiliate › Settings** in the left menu (administrator only).
2. Enter the **shop address**: the only address a referral link can lead to. It must start with
   `https://` (plain `http://` only for a shop on `localhost`).
3. Choose how long the shop **remembers a referral** (30 days by default). The last click wins.
4. **Create the signing secret.** It is shown once; copy it into your shop's configuration. You
   can create a new one at any time; the old one stops working at once.
5. Copy the **code for the shop's pages** into every page of the shop (see below).
6. Configure the shop to send the **webhook** (generic, or Stripe) to the address shown on the
   screen.
7. Press **Send a test event**. It signs a `ping` with your secret and runs it through the real
   webhook; nothing is stored. It works before the programme is on, so you can check the secret
   first (the shop's own requests are still refused with `404` until then).
8. Switch the programme **on**. Partners now see their referral link under **Affiliate** in their
   left menu.

Currency: one per instance, the `currency` setting of the programme (PLN by default). An order in
another currency is refused with `422` by the generic webhook. The Stripe endpoint receives every
sale in the account, so it answers such a session with `200` and stores nothing: `ignored` without
a referral, `unsupported_currency` (and a line in the server log) with one.

## The code for the shop's pages

The snippet (`js/pt-ref.js`, copied from the settings screen with your cookie lifetime filled in)
never contacts PartnerType. It reads `pt_ref` and `pt_click` from the address the visitor arrived
at and keeps them in one first-party cookie, `pt_ref`.

**Consent comes first.** The snippet stores nothing until your shop calls
`PartnerTypeRef.consent()`. Call it on every page load once the visitor has agreed to marketing
cookies (most consent banners offer a callback for this). Call `PartnerTypeRef.clear()` when the
visitor withdraws consent. Mention the cookie in your cookie policy: name `pt_ref`, purpose
"attributing a purchase to the partner who referred you", lifetime as configured.

At checkout, read the referral and send it with the order:

```js
const ref = window.PartnerTypeRef ? window.PartnerTypeRef.get() : null;
// generic webhook: put it into the "ref" field
// Stripe Checkout: client_reference_id: ref  (or metadata: { pt_ref: ref })
```

## Generic webhook

```http
POST /api/affiliate/webhook
Content-Type: application/json
PartnerType-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>." + raw body)>
```

```json
{
  "event": "order.paid",
  "order_id": "A-1001",
  "amount": "199.00",
  "currency": "PLN",
  "ref": "abcde12345",
  "occurred_at": "2026-11-02T10:15:00Z"
}
```

| Field | Rules |
|---|---|
| `event` | `order.paid`, `order.refunded` or `ping` |
| `order_id` | required, up to 128 printable ASCII characters, unique per order |
| `amount` | the **net** amount as a decimal string; required for `order.paid`. For `order.refunded`: the total refunded so far (leave it out for a full refund) |
| `currency` | optional; must be the instance currency |
| `ref` | optional; the value of `PartnerTypeRef.get()` (a partner code or a click id) |
| `occurred_at` | optional ISO 8601 time of the order; defaults to now |

Any other field is refused. Do **not** send the buyer's name, e-mail or address: PartnerType has
no use for them and does not accept them.

| Response | Meaning |
|---|---|
| `200 {"status": "pending", "conversion_id": 12}` | stored; waits for approval |
| `200 {"status": "unattributed", …}` | stored without a partner (unknown, missing or inactive code); the administrator may assign it |
| `200 {"status": "ok"}` | `ping` accepted |
| `200 {"status": "refunded", …}` | an `order.paid` stored, but a full refund for it had already arrived, so it earns nothing; or an `order.refunded` that ended an order not approved yet |
| `200 {"status": "refund_after_approval", …}` | an `order.refunded` for an approved order; the administrator decides |
| `200 {"status": "refund_before_order", "conversion_id": null}` | an `order.refunded` for an order not reported yet; it is kept and applied when the order arrives |
| `400` | invalid body |
| `401` | missing, wrong or old signature (more than 5 minutes off) |
| `404` | the programme is switched off |
| `409` | the same `order_id` was reported with another amount or currency |
| `413` | body larger than 64 KB |
| `422` | another currency |
| `429` | too many requests; retry after `Retry-After` seconds |

Sending the same order again is safe: it answers with the conversion it already created. A repeated
`order.paid`, a partial refund and a refund that changes nothing answer with the order's current
status, which can be any of `pending`, `unattributed`, `approved`, `rejected`, `refunded`,
`refund_after_approval` or `reversed`. Treat every `200` as delivered.

### Signing examples

curl (with `openssl`):

```sh
SECRET='ptw_…'
BODY='{"event":"order.paid","order_id":"A-1001","amount":"199.00","currency":"PLN","ref":"abcde12345"}'
T=$(date +%s)
SIG=$(printf '%s.%s' "$T" "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -hex | sed 's/^.* //')
curl -sS https://partners.example.com/api/affiliate/webhook \
  -H 'Content-Type: application/json' \
  -H "PartnerType-Signature: t=$T,v1=$SIG" \
  --data "$BODY"
```

Python:

```python
import hashlib, hmac, json, time, urllib.request

def send(secret, payload, url="https://partners.example.com/api/affiliate/webhook"):
    body = json.dumps(payload).encode()
    t = str(int(time.time()))
    sig = hmac.new(secret.encode(), t.encode() + b"." + body, hashlib.sha256).hexdigest()
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "PartnerType-Signature": f"t={t},v1={sig}",
    })
    return urllib.request.urlopen(request).read()
```

JavaScript (Node.js 18+):

```js
import { createHmac } from "node:crypto";

export async function send(secret, payload, url = "https://partners.example.com/api/affiliate/webhook") {
  const body = JSON.stringify(payload);
  const t = Math.floor(Date.now() / 1000);
  const sig = createHmac("sha256", secret).update(`${t}.${body}`).digest("hex");
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "PartnerType-Signature": `t=${t},v1=${sig}` },
    body,
  });
  return response.json();
}
```

Sign the exact bytes you send; re-serialising the JSON after signing breaks the signature.

WooCommerce, Shopify and other shops: send this webhook from a small piece of your own code (an
order-paid hook) or from an automation service such as Zapier. No plugin is shipped.

## Stripe

1. In the Stripe Dashboard add an endpoint `https://<your instance>/api/affiliate/stripe` with the
   events `checkout.session.completed`, `checkout.session.async_payment_succeeded` and
   `charge.refunded`.
2. Copy its **signing secret** (`whsec_…`) into **Affiliate › Settings ›
   Stripe signing secret**.
3. Create Checkout Sessions with `client_reference_id` set to `PartnerTypeRef.get()` (or
   `metadata[pt_ref]`).

PartnerType verifies the `Stripe-Signature` header with that secret (five-minute tolerance), reads
only the session id, the amount without tax (`amount_total` minus `total_details.amount_tax`), the
currency, the payment intent and the referral, and ignores every other event. A session that is
not paid yet (delayed payment methods) is recorded when `async_payment_succeeded` arrives. A
refund is matched through the payment intent; a partial refund is converted to the net amount in
proportion. Stripe sends every refund in the account, so one that matches no order is answered
`200 {"status": "ignored"}`; if it is in the instance currency it is kept for a year in case its
session arrives later (see Refunds).

**Subscriptions.** A Checkout session in `subscription` mode has no payment intent of its own, so
its refund is matched through the session's first invoice. Stripe names that invoice in a refunded
charge only on API versions before `2025-03-31.basil`; on newer versions such a refund cannot be
matched and is not detected. For that reason an order from a subscription session is **never
approved automatically**: approve it by hand once you know it was not refunded.

## Approving and paying

**Affiliate › Sales from referrals** lists the orders. Staff can read it; only the administrator
decides.

- **Approve** (one or the selected): the order becomes a **won lead** of the partner
  (`source = affiliate`, the order id instead of client details), with the commission computed by
  the partner's own tier and bonuses, first in the queue. The approval also confirms the win, so the
  commission is payable in **Payouts** like any other.
- **Reject**: the order earns nothing.
- An order of a partner who has since been **deactivated, deleted or erased** cannot be approved,
  by hand or automatically. It stays pending and the list says why. A deactivated partner can be
  reactivated; for a deleted partner, or one whose data was erased on request, reject the order (an
  erased account is never reactivated to be paid).
- **Assign**: an order without a partner goes to the partner you choose and waits for approval.
- **Approve automatically after N days** (off by default; 14 days suggested, your refund window):
  the daily maintenance (`python3 -m tools.instance --env <env> --id <instance> maintenance`)
  approves pending orders older than that, except those with any refund and Stripe subscription
  orders (see Stripe above). The days count from the order time or from when the order reached
  PartnerType, whichever is later, so an order reported late still gets the whole window.

No integration ever changes an approved amount.

## Refunds

| When | What happens |
|---|---|
| Refund before the order itself arrives | kept for 365 days, as long as the shop may still report its order (the webhook refuses orders older than a year); the order then arrives already **Refunded** (or partly refunded, waiting for a person) and is never approved automatically |
| Full refund before approval | the order becomes **Refunded**; nothing is created |
| Partial refund before approval | recorded; approving pays commission on the rest |
| Refund after approval, commission unpaid | the order is flagged **Refunded after approval** and the administrator is notified; **Reverse commission** moves the lead to the lost stage and clears the commission |
| Refund after approval, commission paid | flagged for information only; settle the difference with the partner yourself |

## What is stored

Only the order id, net amount, currency, order time, the partner code or click id and a SHA-256 of
the webhook body. No buyer e-mail, name or IP address, and no webhook body. A click stores a
random click id, the link and the time: no IP address, browser or referrer. Rate limits use a keyed
hash of the visitor's address (for IPv6, of its /64 network, which one host usually holds whole)
that is discarded within two days. Clicks are deleted after 400 days.
A refund that arrives before its order keeps only the order id (or Stripe payment) and the amounts.

## Limits and safety

- Signatures compared in constant time; timestamps more than 5 minutes off are refused.
- Webhook bodies over 64 KB are refused; webhooks and clicks are rate-limited. Only webhook
  requests with a missing or wrong signature are limited per sender, so neither they nor someone
  else's traffic from the same address (Shopify, Zapier and Make deliver many shops' webhooks from
  shared addresses) can use up the limit your signed deliveries rely on.
- Click limits never turn a buyer away: many real buyers share one address (mobile carriers,
  offices, public Wi-Fi). A visitor who clicks too often, or any visitor while the whole instance
  is too busy to count clicks, still reaches the shop with the partner code; only the click goes
  uncounted.
- `/r/<code>` redirects only to the configured shop address; the code is its only input. A link of
  an inactive partner, a revoked link, or a programme that is off answers 404.
- The signing secrets are stored encrypted with a key derived from the instance session secret.
  Rotating the session secret makes them unreadable: create a new signing secret afterwards.
- The AI assistant and the MCP gateway can only read orders; approving is always a person's click.

Out of scope in this version: shop plugins, more than one currency, commission on every recurring
invoice of a subscription, coupon codes as referral codes, and fraud detection beyond the limits
above.
