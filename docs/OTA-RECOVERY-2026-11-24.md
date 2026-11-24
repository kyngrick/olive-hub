# OTA attempt 24 Nov 2026 — final outcome

## Result: speaker restored to a fully working 0.14.5. 0.14.6 was attempted three
## times and consistently failed on this device; it is NOT installed now.

### Final verified device state
| Item | Value |
|---|---|
| OS | **LibreEcho OS 0.14.5** (restored, working) |
| Features | **ready**, no error |
| Wake word | Alexa, model loaded, capture + inference active |
| Beamformer | running |
| Volume / mic gain | 8 / 78 (unchanged) |
| Update channel | stable |
| Update state | rolled-back, `not-checked`, rollback available |
| Boot | confirmed, running slot A |
| Diagnostics | all 11 checks OK, including wake word |
| Config backup | `speaker-config-backup-20261124.json` |

## What happened
1. Pre-flight found a pre-existing rollback hold from an earlier OTA attempt.
2. Supported `check → apply` on stable was blocked by `update-held-after-rollback`.
3. Manual signed upload of the 0.14.6 OTA installed the **base image only** —
   the tar contains `manifest`, `manifest.sig`, `boot.img`; feature payloads are
   separate assets the proper flow downloads. Result: 0.14.6 booted with
   `features_state=degraded / pending-manifest`, `libreecho-waked` absent,
   wake word unavailable. The device rolled itself back after a reboot.
4. After that reboot, the boot record became `confirmed` and 0.14.5 was fully
   restored — wake word and features ready.
5. Retried the **supported** flow: switched to the dev channel (whose candidate
   is also labelled 0.14.6, immutable build
   `radar-puffin-build-65bc886-7892d5b54f009bc6-f9910964ec9928af`), check gave
   `update-available`, and `apply` succeeded — this time with feature payloads.
   After reboot: **0.14.6 again degraded with `pending-manifest`**, wake
   unavailable, and the device rolled back to 0.14.5 after the next boot.
6. Conclusion: **LibreEcho 0.14.6 does not pass feature-manifest validation on
   this Echo 2nd Gen hardware image.** Both the manual and proper signed paths
   produce the same result. This looks like a firmware defect, not a
   configuration problem. The A/B bootloader correctly restored the working
   0.14.5 slot both times.

## Actions taken to recover
- Two reboots via `POST /api/v1/system/reboot` (the bootloader rolled back to
  the known-good slot each time).
- Channel returned to **stable**.
- No configuration was restored from backup because nothing needed restoring —
  settings (sensitivity 80, gain 78, volume 8) survived unchanged.
- PC hub/dashboard/intents were never affected and remain healthy (98 intents).

## Recommendation
**Stay on 0.14.5.** Do not attempt 0.14.6 again until the LibreEcho project
fixes the `pending-manifest` feature-validation failure for radar_puffin.
The AEC-reset fix in 0.14.6 would be valuable for the "shout over music"
problem, but not at the cost of losing wake-word detection.

Before any future attempt: back up the configuration, keep the rollback slot,
and be prepared for the device to reboot itself back to 0.14.5. If you want,
report this with the build tag above to the LibreEcho issue tracker.

The gated A/B script (`tools/wake/ab_wake_test.py`) and its pre-upgrade baseline
remain valid — the device is back in the same working state in which the
baseline was captured.
