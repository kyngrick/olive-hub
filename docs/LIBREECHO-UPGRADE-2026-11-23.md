# LibreEcho upgrade assessment — 23 Nov 2026

## Research sources
- `libreecho.org` — current product site, last updated 17 Nov 2026 (authoritative).
- `github.com/aslater3/LibreEcho/releases` — release list and full 0.14.6 notes.
- `dev.libreecho.org` — **stale**: still pinned to 0.13.15, "status reviewed 23 September 2026". Do not use it to decide the current version.

## Findings

| Item | Value |
|---|---|
| Latest stable | **0.14.6** — released 17 Nov 2026, tag `radar-puffin-v0.14.6` |
| Currently installed | 0.14.5 |
| Upgrade path | In-place signed OTA, same 0.14 line — **no reinstall, no cable** |
| Update format change | Only 0.13 → 0.14 required a reinstall; 0.14.5 → 0.14.6 does not |
| Rollback | Signed A/B slots; speaker rolls back automatically if new slot fails to start |

## Verdict: safe to upgrade, with one important rule

0.14.6 replaces 0.14.5 and explicitly "includes everything in it". It is a
feature + fix release on the same line, installed through the signed A/B
updater. Nothing in the release notes changes the Wyoming STT/TTS or
assistant-endpoint contract that Olive Hub depends on. The only hard rule:

> **Update from the speaker control centre via
> System → System update → Check for updates.**
> The release notes state that uploading the update file by hand "doesn't work
> for new builds yet". Do not sideload the OTA file.

## Why this upgrade is actually useful for our setup

These 0.14.6 fixes hit the exact integration points we use:

- **Sendspin**: music picks up again by itself after a voice reply or a mute
  instead of staying silent. We already rely on Music Assistant/Sendspin with a
  native announce fallback when the Sendspin client does not start streaming.
- **Wi-Fi**: fixes 10–30 second pauses that could interrupt streaming and voice
  replies.
- **Updates**: an update interrupted by a restart no longer blocks later
  updates with "update busy"; "Restart into update" is offered again.
- **Home Assistant voice**: replies stream (no speaker power-up delay or crack),
  and announcements play even when the pre-announce chime cannot be fetched.
- **Voice**: cancel a request while listening/thinking/replying, separate reply
  volume, custom sounds and custom wake-word models (all optional/off by default).
- **Time/alarms**: time zone setting, alarms with repeat days and snooze, spoken
  reminders — relevant if you use Olive's timer/reminder intents.

## Pre-upgrade checklist (5 minutes)

1. Confirm the device is an **Amazon Echo 2nd Gen** (tall fabric cylinder, 2017,
   codename radar/puffin). 0.14.6 also covers Echo Dot 2nd Gen as experimental.
   Stop if it is any other model.
2. In the speaker control centre: **System → Backup/Restore → back up the
   current configuration**.
3. Confirm the inactive A/B slot is healthy and do not disable rollback.
4. Note the current settings that Olive Hub depends on:
   - STT / Wyoming ASR: `tcp://<PC-IP>:10300`
   - Assistant endpoint: `http://<PC-IP>:8765/v1`, model `olive`, key `HUB_TOKEN`
   - TTS: `http://<PC-IP>:10200` (language router → Piper/Kokoro)
   - Announce path used for spoken replies
5. Do it when you can spend ~10 minutes testing afterwards.

## Upgrade steps

1. Open the speaker's control centre on the LAN.
2. **System → System update → Check for updates** → install 0.14.6.
3. Wait for reboot; verify version 0.14.6 and active slot in the control centre.
4. Run the post-upgrade checks below. If anything critical fails, use the
   documented rollback to the previous slot rather than reflashing.

## Post-upgrade smoke test (Olive-specific)

```powershell
# 1. Hub still healthy on the PC
$h = Invoke-RestMethod http://127.0.0.1:8765/health
$h.ok
$h.whatsapp_bridge.status

# 2. Dashboard still 9/9 services
$tok = ((Get-Content .\.env | Where-Object {$_ -match '^HUB_TOKEN='}) -replace '^HUB_TOKEN=','').Trim()
$s = Invoke-RestMethod http://127.0.0.1:8767/api/summary -Headers @{'X-Olive-Token'=$tok}
$s.services | Where-Object { -not $_.up }   # should print nothing
```

Then speak to the speaker:

1. "Alexa, what time is it?" → spoken reply, hub log shows the `time` intent.
2. "Alexa, play BBC 1Xtra" → radio starts; say a command and confirm music
   resumes (the Sendspin fix).
3. "Alexa, che ore sono?" → Italian path still reaches Piper IT / Kokoro IT.
4. "Alexa, speak like KokoroEN" → TTS router still reaches the WSL service.
5. Check `whatsapp status` and a timer/reminder if you use them.

No `.env` changes should be needed on the PC. If the speaker's IP changes, only
the speaker-side STT/assistant/TTS URLs need updating, not Olive Hub.

## Known issues that remain after upgrading

- AirPlay **from Music Assistant** still needs Sendspin, or the speaker's
  AirPlay mode set to "AirPlay 2 - compatibility mode" or "AirPlay 1 (RAOP)".
  AirPlay from Apple devices is unaffected.
- The anonymous weekly check-in (model + version only) can be switched off at
  **Privacy → Active-device count** (available since 0.14.5).
- Microphone mute is still a software mute, not a proven hardware-level
  privacy switch.
- Experimental extras (noise suppression, second/server-side wake word,
  Bluetooth proxy, volume leveling) are off by default — leave them off unless
  you are deliberately testing.

## Bottom line

Upgrade to 0.14.6 is **low risk and recommended** for this setup: same release
line, signed A/B rollback, and it fixes Sendspin/Wi-Fi behaviour we currently
work around. Use **Check for updates** in the control centre, back up first, and
run the smoke test above. I have not performed the upgrade — the decision and
the physical control-centre step are yours.
