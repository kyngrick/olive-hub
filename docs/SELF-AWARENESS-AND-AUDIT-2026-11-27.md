# Self-awareness, memory and pipeline audit — 27 Nov 2026

## What Olive actually had in memory
`olive_memory.json` held only four facts (birthday, "punch: nice!", "likes you",
one garbled note) and 12 topics, several of them ASR noise. Nothing recorded
who Ricky is, that she is Olive, or that we built and maintain her. No document
about her own architecture reached the LLM.

## Implemented now
1. **Self-knowledge files** — `olive_self.md` (full identity, architecture,
   user profile, chronological work log) and `olive_self_brief.txt` (compact).
   The brief is injected into every LLM system prompt in `llm_answer()`.
2. **Grounded self answers** — new `self.about` intent answers "who are you",
   "what do you know about me", "who built you", "what can you do", "chi sei"
   by reading sections of `olive_self.md` plus `memory.context()`, not by
   hardcoded strings. Moved ahead of the generic `help` intent so the richer
   capability answer wins.
3. **Seeded memory facts** — local-only facts: my name, user name, timezone,
   music taste, collaborator, speaker. Gitignored `olive_memory.json`.
4. **Live context in prompts** — current date/time and pending alarm/reminder
   counts are added to the LLM system prompt, reducing hallucinated state.
5. **Voice LLM fallback** — `/command` now uses the same intent-then-LLM chain
   as `/v1/chat/completions` for unmatched speech instead of returning 404.
6. **Caller history** — `/v1/chat/completions` now feeds prior user/assistant
   messages from the request into the LLM history instead of only the last
   user line.
7. Fixed a stale hard-coded intent count in the brief.

## Audit findings (126 built-in intents, 0 active custom intents)
- Router: fastpath → STOP → pending clarification → first-match intents →
  music heuristics → None. First-match ordering matters (fixed `help` shadow).
- Memory: `PermanentMemory` with atomic writes, 200 facts / 60 notes / 12
  topics; `context()` injects at most 18 facts, 8 notes, 6 topics (1200 chars).
  Learning happens only via explicit "remember"/"my X is Y"/"I like X",
  absorbed topics from expired threads, and music taste counters.
- Conversation is in-memory only: 120 s TTL, 10 turns. No persistent dialogue
  transcripts, no per-user sessions, no feedback/rating path.
- No retrieval over Olive's own project docs; no embeddings or vector search.
- `/v1/chat/completions` previously dropped all caller history; fixed.
- Memory pollution remains: ASR-garbled topics and junk facts are injected
  into every prompt; `recall()` substring matching can return unrelated facts.

## Verified — live, 27 Nov 2026
- "who are you" → grounded identity from `olive_self.md`.
- "what do you know about me" → user profile plus memory facts.
- "who built you" → actual work-log excerpts.
- "what can you do" → `self.about` (was shadowed by `help`), 126 intents.
- Unmatched speech "Give me one short tip for keeping houseplants alive" →
  `llm` reply instead of 404.
- 12/12 tests pass; hub healthy.

## Research: how to make her smarter next
- Local retrieval over project docs and past conversations (keyword/BM25 first,
  embeddings later) so she can answer "how does X work?" from real docs.
- Feedback learning: "that's wrong, remember Y" → write a fact with a
  confidence score; downvote noisy topics automatically.
- Nightly distillation of absorbed topics into durable, deduplicated facts.
- Function/tool calling for live state (time, lights, scheduler, dashboard)
  instead of relying on pattern coverage.
- Per-user/session isolation for chat and voice, with scoped memory.
- Intent analytics from `olive_hub.err` traces to find frequently missed
  phrases and add intents where deterministic answers are better than an LLM.
