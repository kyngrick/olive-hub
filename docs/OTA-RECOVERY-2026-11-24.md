# OTA attempt 24 Nov 2026 — status and recovery

## Outcome: NOT successful. Device voice is currently degraded.

I attempted the authorised 0.14.6 upgrade. The PC-side hub is fine, but the
speaker is running a **base 0.14.6 image without its feature payloads**, so the
wake-word service is down. **Do not treat the speaker as voice-operable until
the recovery steps below are done.**

## What happened (chronological)
1. Backed up the speaker configuration via `GET /api/v1/config/export` to
   `speaker-config-backup-20261124.json` (1517 bytes). Pre-flight showed
   **0.14.5**, slot A, and a pre-existing `rolled-back` / `update-held-after-rollback`
   state from an earlier failed OTA.
2. `POST /system/update/check` worked but the device kept reporting
   `update-held-after-rollback` (the firmware suppresses replay of a candidate
   that previously rolled back). `POST /system/update/apply` therefore returned
   409 "No checked update is currently available".
3. Downloaded the official signed 0.14.6 OTA (16,793,600 bytes) and used
   `POST /system/update/upload`. It returned `installed: true, reboot-pending`.
   I rebooted via `POST /system/reboot`.
4. After reboot the device reports **LibreEcho OS 0.14.6**, but:
   - `features_state = degraded`, `features_error = pending-manifest`
   - wake word: `available: false` — "Wake-word service is not available"
   - `libreecho-waked` is **not running**; `/microphones/beams` is not running
   - `state = failed`, `check_status = update-held-after-rollback`
   - boot record `state = pending`, `confirmed = false`, `running_slot = a`,
     `running_slot_tries = 0`
5. Cause: the official OTA tar contains only `manifest`, `manifest.sig`,
   `boot.img`. The **feature payloads** (wake word, STT, assistant) are separate
   assets that the normal "check → apply" flow downloads before installing.
   The manual upload path skipped them. This matches the 0.14.6 release-note
   warning: *"install this update with Check for updates — uploading the update
   file by hand doesn't work for new builds yet."*
6. Tried to repair via the proper flow:
   - dev channel gave `update-available` (candidate `radar-puffin-build-65bc886…`,
     also labelled 0.14.6); `apply` failed with
     "The signed update failed verification or installation".
   - back on stable, check is held again; apply refuses.
   - restoring 0.14.5 by signed upload failed with
     `boot_store_not_committed` because the boot record is pending.
   - `libreecho-bootctl confirm a` says already confirmed; `confirm b` fails with
     "BCB update/readback failed". No HTTP endpoint exists to confirm/boot-select.
   These operations need root on the device; the SSH account is unprivileged.

## Current state (verified)
| Item | Value |
|---|---|
| OS | LibreEcho OS 0.14.6 (base image only) |
| Features | degraded / `pending-manifest` |
| Wake word | **unavailable** — device cannot hear "Alexa" |
| Volume / mic gain | 8 / 78 (unchanged) |
| Update state | failed, held after rollback, rollback_available = true |
| Boot | pending, not confirmed, running slot A, 0 tries left |
| PC hub | healthy: 98 intents, chat/API/dashboard working |
| Config backup | `speaker-config-backup-20261124.json` |
| Downloaded OTAs | `libreecho-stable.ota.tar`, `libreecho-0145.ota.tar` in `%TEMP%\opencode` |

## Recovery steps (need your action in the speaker control centre)
1. Open `http://192.168.1.228:8080/` → **System / About**.
2. Use the **rollback / previous slot** control, or reboot into the previously
   working 0.14.5 slot. `rollback_available` is still true. If the UI offers
   "Confirm this boot" / "Restart into update", use it to clear the pending
   boot record first.
3. Once the device is back on **0.14.5 with features ready** (wake word returns),
   confirm the boot from the control centre so the boot record is `confirmed`.
4. Then run the supported path: **System → System update → Check for updates**
   and install 0.14.6 from there. Do **not** hand-upload the OTA file.
5. Verify: `/api/v1/status` → `features_state: ready`;
   `/api/v1/wake-word` → `available: true`; say "Alexa" once.

If the control centre cannot clear the pending boot record, a power-cycle may
be required, followed by the rollback. Keep the configuration backup handy.

## PC side
No hub changes were needed. The hub, dashboard, intents and APIs remain healthy
and were not affected by the speaker's state. The A/B script baseline file
(`wake-ab-results.json`) was captured before the OTA and is still valid as a
pre-upgrade baseline once voice is restored.

## Lesson
The pre-existing `update-held-after-rollback` state should have stopped the
attempt. Manual OTA upload is not a valid substitute for the signed check/apply
flow on 0.14.6 — it installs only the base image.
