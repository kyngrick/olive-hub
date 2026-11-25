# WhatsApp send verified — 25 Nov 2026

## Why it was "trouble"
Two separate problems were present:
1. The bridge was wedged on stale Chrome profile locks and an outdated
   `whatsapp-web.js` injection method (fixed earlier today; QR re-linked).
2. Some natural phrasings such as *"Set WhatsApp message to Giulia too."* did
   **not** match the `whatsapp.send` intent, so the request reached the LLM,
   which honestly but wrongly answered *"I can't send WhatsApp messages."*
   That is what made it look like sending was broken even after the bridge
   was healthy.

## Fixes applied today
- `whatsapp-bridge/server.js`:
  - added `/me` (returns linked number/name for diagnostics);
  - made message IDs robust to WhatsApp's `_serialized` → `$1` rename;
  - kept `/send` unchanged functionally.
- `olive_hub.py`:
  - widened `whatsapp.send` patterns to accept
    `set/write/create a WhatsApp message to … saying …`,
    `WhatsApp message to … saying …`, and no-body forms that ask for the text;
  - stripped trailing "too/also/please" from the contact name;
  - handler fallback regex now also accepts set/write/create;
  - system prompt now states WhatsApp/alarm/timer/reminder capabilities are
    working local capabilities, so the LLM does not falsely deny them.
- `ensure-hub.ps1` reads the bridge port from `.env` (3015).

## Verification (real sends, to the linked user's own number only)
Linked account from `/me`: `+39 348 276 3421` ("Kyng Rick"). No external contact
was used or authorised.

1. Direct bridge:
```json
POST /send {"to":"+393482763421","text":"Olive bridge test - 25 Nov 2026"}
→ {"ok":true,"id":"true_195060419309807@lid_3EB0E8154022C59F31F07D_out","to":"393482763421@c.us"}
```
   `/health` counter `sent: 0 → 1`.

2. Full voice/hub pipeline:
```
"send a whatsapp message to +39 348 276 3421 saying Olive hub pipeline test"
→ [whatsapp.send] "WhatsApp message sent to plus three nine, ... It said: olive hub pipeline test."
   bridge sent: 1 → 2
```

3. After the intent-pattern fix:
```
"Set WhatsApp message to Giulia too."
→ [whatsapp.send] "What should the message say?"   (no false LLM denial, nothing sent)

"set whatsapp message to +39 348 276 3421 saying Olive final verification"
→ [whatsapp.send] "WhatsApp message sent to plus three nine, ... It said: olive final verification."
   bridge sent: 2 → 3
```

Bridge health after all tests: `status=ready`, `lastError=""`, `sent=3`.

## Delivery claim — precise
The bridge returned a real WhatsApp message ID and its `sent` counter increased
for each send. That confirms WhatsApp accepted the messages for delivery to the
linked account. This bridge does **not** expose per-message read/delivery
receipts, so I am not claiming the phone displayed them — please check your
own WhatsApp chat for the three test messages.

## Final state
Hub healthy, WhatsApp `ready` on port 3015, session9 linked. No external
recipient was messaged. Old `whatsapp-web.js` package backed up as
`node_modules/whatsapp-web.js.bak-20261125`.
