# Lightweight TTS / voice-cloning candidates (22 Nov 2026)

Researched against Olive's requirements: CPU-only, small RAM footprint, Italian
support, voice cloning from a short sample, and a path into the existing Wyoming
TTS router. This PC currently has **~0.9 GB free RAM** (7.4 GB total, heavily
loaded), which rules out the multi-gigabyte options.

## Comparison

| Project | Size / params | Cloning | Languages | Licence | Fit for Olive |
|---|---|---|---|---|---|
| **Pocket TTS** (Kyutai) | 100M; tiny English models 1.9M–18M; standalone Windows binary **7.9 MB** | Yes, from a WAV; can export a reusable `safetensors` voice embedding | EN, FR, DE, PT, **IT**, ES, NL (+ community models incl. Japanese) | MIT (code); voice/model terms on HF | **Best first experiment.** Tested here. |
| **Sopro TTS v2 turbo** | 120M, ONNX, streaming ~300 ms | Yes, 5–20 s reference | EN, PT, FR, DE — **no Italian** | Apache 2.0 | Good small cloner, but cannot be the Italian main voice. |
| **OpenVoice V2** (MIT/MyShell) | moderate, needs a base TTS (VITS-style) | Yes, cross-lingual tone cloning + style control | EN, ES, FR, ZH, JA, KO — **no Italian** | MIT | Powerful stylistically, but no Italian and heavier stack. |
| **KittenTTS 2 Ternary** | 1.7B, ~450–492 MB, CPU needs ~6 GB RAM | Yes, 5–30 s + expression tags | 20 languages incl. **IT and JA** | Code Apache 2.0; **model has separate licence** | Best language coverage but **too large for this PC right now**. Small Pico/Nano/Mini models have preset voices only. |
| **Hojo-TTS-Light** | 40M / 80M, ONNX CPU | 80M: yes, few seconds | Chinese, English only | Apache 2.0 | Very light, but no Italian. |
| **VoiceForge** | — | — | — | — | GitHub search found only small/unrelated web-UI repos; not a serious model candidate. |

## Hands-on results — Pocket TTS

Downloaded the official standalone binary (no Python/PyTorch needed):

```
pocket-tts-3.3.0-x86_64-pc-windows-msvc.zip  (7.9 MB)
```

Verified:

- **Tiny English model** (`english_2m_2026-10`): 2.9 s of audio generated in
  **180 ms** (16× real-time), WAV written successfully.
- **Italian model** (`italian`, 100M): *"Buongiorno, sono Olive. Che ore sono?"*
  generated 3.4 s of audio in 2.7 s (**1.29× real-time**) — fast enough for
  short spoken replies.
- **Cloning** from `test_input.wav` with the Italian model: blocked because the
  voice-cloning weights are Hugging Face–gated. The CLI says to accept the terms
  at <https://huggingface.co/kyutai/pocket-tts> and run `hf auth login`.
  Built-in voices (including Italian `giovanni`) work without that step.
- `export-voice <audio> <out.safetensors> --language italian` is available to
  precompute a voice embedding once and reuse it with fast loading.

Other useful facts:

- There is a community **Wyoming server** for Pocket TTS:
  `ikidd/pocket-tts-wyoming` (Docker image `ghcr.io/ikidd/pocket-tts-wyoming`),
  with `WYOMING_PORT`, `DEFAULT_VOICE`, `MODEL_VARIANT=italian`.
- Pocket TTS can be used from Python (`TTSModel.load_model()`,
  `get_state_for_audio_prompt()`, `generate_audio()`), or via local HTTP `serve`.
- The model README prohibits cloning without explicit consent.

## Recommendation

1. **Pocket TTS is the first choice.** It is small, CPU-fast, has native Italian,
   and both built-in and cloned voices can be exposed through a Wyoming server.
2. If a cloned English voice is wanted sooner, **Sopro** is the lightest cloner,
   but it cannot speak Italian.
3. If more RAM becomes available, **KittenTTS 2 Ternary** is the strongest
   multilingual option (Italian and Japanese, expression control, 5–30 s cloning).
4. OpenVoice and Hojo-TTS-Light are lower priority because neither covers Italian.

## Integration path into Olive (without breaking speech again)

Do **not** point the speaker at a new port while Windows Firewall has no rule
for it — that is what broke speech when 10205 was tried. Instead:

1. Run Pocket TTS (binary or Wyoming Docker container) on a spare local port,
   e.g. 10206, and test it with a local Wyoming client first.
2. Add a `pocket` provider to `voice_personas.py` / the TTS router pointing to
   that port. Keep the active persona `ready: false` until the service is verified.
3. Once verified, either:
   - add an elevated Windows Firewall allow rule for 10206, then use
     `switch-olive-endpoint.ps1 -Endpoint 192.168.1.86 -TtsPort 10206`, or
   - wait for a reboot, when the persona-capable router starts on 10200, and let
     the router forward Pocket voices without changing the speaker's endpoint.
4. For cloning, accept the HF terms, run `hf auth login`, then
   `pocket-tts export-voice <ref.wav> <name>.safetensors --language italian`
   and register that persona.

Nothing in this research changed the live speaker configuration; speech remains
on the verified 10200 English path.

## Follow-up: Hugging Face terms + ZONOS3-Mini

### Accepting the Hugging Face terms (needed for Pocket TTS cloning)

1. Sign in or create a free account at <https://huggingface.co>.
2. Open the gated model page: <https://huggingface.co/kyutai/pocket-tts>.
3. Read the terms and click **"Agree and access repository"** (sometimes shown
   as *Request access*). Approval is normally immediate.
4. Create a read-only token at <https://huggingface.co/settings/tokens>:
   **New token → Read → Generate**.
5. On the PC, log the CLI in once:
   ```powershell
   .\.venv\Scripts\python.exe -m pip install -U huggingface_hub
   .\.venv\Scripts\hf.exe auth login
   # this project's hub venv already has huggingface_hub 2.4.0 installed.
   # older installs may use: huggingface-cli login
   ```
   Paste the token when prompted. Alternatively set `HF_TOKEN=hf_...` in the
   environment of the process that runs Pocket TTS.
6. Then `pocket-tts generate --voice <your.wav>` and `export-voice` work.

Do not paste your HF token into chat. For unattended runs, store it in `.env`
and have the launch script set it for the child process.

### ZONOS3-Mini (Zyphra) — researched, not installed

<https://huggingface.co/spaces/Zyphra/ZONOS3-Mini> and model
`Zyphra/ZONOS3-Mini` are **public / not gated**, Apache-2.0 weights, MIT code.

| Property | Value |
|---|---|
| Active parameters | ~100M (800M total latent MoE, 64 experts, top-2) |
| Languages | **43**, including Italian, Japanese, English and most European/Asian languages |
| Cloning | Raw reference clip (up to 60 s) **or** a 192-d speaker embedding; no transcript required |
| Built-in voices | 27, including Italian `Ginevra` and `Lorenzo` |
| Extras | Emotion/accent/pace steering, word-level emphasis, speech editing, streaming |
| Backends | CUDA (Mini-SGLang), MLX (Apple), **C++/ggml CPU**, browser WebGPU |
| Size | GGUF ~663 MB backbone + codec/encoder files; PyTorch weights ~9 GB; Space downloads ~10.5 GB |
| License | Apache-2.0 weights, MIT runtime code |

Verdict: **better in capability, not lighter** than Pocket TTS. It is more
multilingual and ungated, but the local CPU route needs the ggml server built
from source (Rust + CMake toolchain; Rust is present here, CMake is not on PATH)
and the GGUF download is roughly 1 GB. With only **~0.6 GB free RAM** on this PC
right now, the ggml server would likely not fit alongside the hub, Whisper,
Music Assistant and the WhatsApp bridge.

Recommendation unchanged: **Pocket TTS first** (small, tested, fast). Upgrade to
ZONOS3-Mini when more RAM is available or when its 43-language coverage and
ungated cloning justify the heavier setup. The hosted HF Space is useful for
listening to voice quality but is not a low-latency local replacement.
