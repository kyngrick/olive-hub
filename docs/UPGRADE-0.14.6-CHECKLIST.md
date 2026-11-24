# LibreEcho 0.14.6 upgrade checklist (approval-gated)

Prepared 24 Nov 2026. **Nothing here has been executed.** The speaker is still
on **0.14.5**. Upgrading is a signed in-place OTA with A/B rollback, but it is
your action in the speaker's control centre.

## Why upgrade
The 0.14.6 release notes include: *"the wake-word echo canceller resets when
music starts or its reference stream has gaps"* (LibreEcho-UI #519, #526).
Our live 0.14.5 device lacks the corresponding AEC guard (no
`/run/libreecho-audio/waked-aec.status`), and measured wake confidence drops
from median 0.998 (music off) to 0.757 (music on). This is the leading cause of
having to shout over music.

## Pre-flight (read-only, already done where marked)
- [x] Confirm model: `GET /api/v1/device` → `LibreEcho OS 0.14.5`.
- [x] Confirm current settings: wake sensitivity 80, mic gain 78, volume 8-25.
- [x] Confirm AEC reference socket is connected (`/proc/net/unix`).
- [x] Confirm stock model is `alexa_v0.1`, no staged models.
- [ ] **You**: back up the speaker configuration from the control centre
      (Settings → Backup/Restore). Do this first.
- [ ] **You**: note the current volume, wake sensitivity, mic gain and any
      custom sounds/stations so they can be restored if needed.
- [ ] **You**: make sure the speaker has stable power and Wi-Fi, and that you
      will not need it for ~15 minutes.

## Baseline (before upgrade)
Run the gated A/B script in baseline mode:
```powershell
.\.venv\Scripts\python.exe tools\wake\ab_wake_test.py baseline --approve-baseline
```
It writes `wake-ab-results.json` and does not change anything.

## Upgrade (manual, your approval)
1. Open the speaker control centre: `http://192.168.1.228:8080/`.
2. **Settings → System → System update → Check for updates**.
3. Install **0.14.6**. Do **not** hand-upload the OTA file — the release notes
   say manual upload does not work for new builds.
4. Wait for the reboot and confirm the version shows **0.14.6**.
5. Confirm the active slot and that rollback is available.

## Immediate post-upgrade checks
- [ ] Wake word still works: say "Alexa" once in a quiet room.
- [ ] `GET /api/v1/wake-word` → model loaded, capture/inference active.
- [ ] `GET /api/v1/microphones/beams` → capture running.
- [ ] Run the A/B script post mode:
```powershell
.\.venv\Scripts\python.exe tools\wake\ab_wake_test.py post --approve-post
```
- [ ] Compare `wake-ab-results.json` baseline vs post sections.

## Optional 0.14.6 features (evaluate one at a time, not during the upgrade)
- **Wake near-miss clips** (Privacy, experimental): best way to see why a
  quiet wake failed with music playing. Keep clips local and delete them after.
- **Wake word models** API: `GET /api/v1/wake-word/models` should now work.
  Do not select a custom model until one exists and has been host-scored.
- **Noise suppression** (Audio, experimental): note it affects speech sent to
  the assistant, **not** the wake word (per API docs), so it will not fix
  wake-up by itself.
- **Microphone mixing**: try only if beamformer results remain poor.

## Rollback
- Control centre → System → select the previous slot / rollback, or let the
  A/B mechanism roll back automatically if 0.14.6 fails to boot.
- Restore the saved volume, wake sensitivity and mic gain afterwards.
- The stock `alexa_v0.1` model is untouched by the upgrade.

## Acceptance
Pass only if: normal-volume "Alexa" works at 3 m with medium music, zero
music-only false wakes in 10 minutes, and zero self-trigger events. Otherwise
keep 0.14.5 (which is working) and report the measurements before any further
change.
