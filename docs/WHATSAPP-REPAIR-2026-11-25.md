# WhatsApp bridge repair — 25 Nov 2026

## Symptom
Bridge was `status=starting` with
"The browser is already running for ...session4... Use a different userDataDir".
All saved sessions (session/2/3/4) had stale Chrome processes holding their
profiles; those processes are protected and cannot be killed from this shell.

## Root cause (two layers)
1. Stale Chrome locks on every saved session profile.
2. After moving to a clean session, `whatsapp-web.js` 1.34.8 (npm release) still
   timed out in `Client.inject`: it waits for
   `window.require('WAWebSocketModel').Socket.state`, which current WhatsApp Web
   no longer exposes. Upstream fixed this on **24 Nov 2026** in commit
   `38eb45b` ("fix: Socket"), after the npm release.

## Fix applied (local, free)
- Installed `whatsapp-web.js` from upstream GitHub main
  (`git+ssh://git@github.com/wwebjs/whatsapp-web.js.git#38eb45b…`).
  Backup of the old package: `node_modules/whatsapp-web.js.bak-20261125`.
- Created a clean session directory `whatsapp-bridge/session9`.
- Changed `.env`:
  - `WHATSAPP_BRIDGE_SESSION=session9`
  - `WHATSAPP_BRIDGE_PORT=3015`
  - `WHATSAPP_BRIDGE_URL=http://127.0.0.1:3015`
- Updated `ensure-hub.ps1` to read the WhatsApp port from `.env` instead of
  hardcoding 3014.
- Restarted the bridge and the hub.

## Current state
Bridge health: `{"ok":true,"status":"qr","lastError":"","qrAvailable":true}` —
the injection failure is gone and a fresh QR is being generated. The QR is shown
in the Review pane from `whatsapp-bridge/qr-live.html` (auto-refreshing).

**Pending user action:** scan the QR with the phone's WhatsApp
(Settings → Linked devices → Link a device). Until that is done the bridge is
not linked and no message can be sent.

No external message has been sent. No recipient has been chosen or authorised.
