# Pronunciation table — brand name

## What is wrong today

The table currently attached to both agents has two faults, and together they
are exactly why the English agent "says kejue in a weird way":

1. **It keys on the old brand name.** The entries are `Cadway → Keh-jway` and
   `Kedway → Keh-jway`, left over from when the project used the placeholder
   name "Cadway". The agents now write the literal string **Kejue**, which
   matches no entry at all — so nothing is rewritten and the voice reads the
   raw spelling. English TTS sees `-jue` and guesses, producing "ke-JOO-ay" or
   "keh-jew", which is the weird reading being reported.

2. **The replacement itself was wrong.** Even when `Cadway` did match, it was
   rewritten to `Keh-jway` — one syllable, a "dway" glide. That is not the
   brand name either. It is also what pushed the Arabic agent into saying
   **كيجواي / "Kejwayh"**, which is visible in the early Arabic transcripts.

The correct pronunciation is **"kay-joo"** — two syllables, stress on the
first, silent final `e`. Sonus' own docs use this exact word as their
respelling example (`/agents/pronunciation`).

## How the table behaves

From `/agents/pronunciation`, the rules that shape the entries below:

- Entries are **plain text**. No IPA, no phonemes, no SSML. Respelling only.
- Entries run **in array order**, first match wins, and **replacement output is
  never rescanned** — so `Kejue → kay-joo` cannot loop or re-trigger.
- Matching is **case-insensitive** by default, so one entry covers `Kejue`,
  `kejue`, and `KEJUE`.
- `match: "word"` (the default) will not fire next to a letter or digit, which
  is what we want — it still matches `Kejue.` and `Kejue's`.
- Only the **TTS-bound stream** is rewritten. Transcripts and extraction keep
  the real spelling, so the dashboard and `call_summary` still read "Kejue".

## Table: `kejue-brand-pronunciation`

**Name:** Kejue Brand Pronunciation
**Slug:** `kejue-brand-pronunciation`
**Description:** Forces the correct two-syllable reading of the Kejue brand
name and absorbs the spellings the LLM and STT produce.

Entries, in this order. Order matters: the possessive and the longer
misspellings must come before the bare name.

| # | From | To | Match | Why |
|---|------|-----|-------|-----|
| 1 | `Kejue's` | `kay-joos` | word | Possessive, before the bare form |
| 2 | `Kejue` | `kay-joo` | word | **The main fix** |
| 3 | `Kejwayh` | `kay-joo` | word | Produced by the Arabic agent previously |
| 4 | `Kejway` | `kay-joo` | word | Same family |
| 5 | `Keh-jway` | `kay-joo` | anywhere | The old wrong replacement, if still live |
| 6 | `Kedway` | `kay-joo` | word | Legacy entry |
| 7 | `Cadway` | `kay-joo` | word | Old project placeholder |
| 8 | `Kajue` | `kay-joo` | word | Common LLM typo |
| 9 | `Kejoo` | `kay-joo` | word | STT variant |
| 10 | `Keju` | `kay-joo` | word | Truncation |
| 11 | `K-Jue` | `kay-joo` | anywhere | Hyphenated variant |
| 12 | `كيجواي` | `كيجو` | word | Arabic agent's wrong form |
| 13 | `كيجوايه` | `كيجو` | word | Arabic variant |
| 14 | `كيجوي` | `كيجو` | word | Arabic variant |
| 15 | `كادواي` | `كيجو` | word | Legacy Arabic placeholder |
| 16 | `كيدواي` | `كيجو` | word | Legacy Arabic placeholder |

Entries 12–16 only normalise *wrong* Arabic spellings onto the correct one.
`كيجو` itself is deliberately **not** an entry — the Arabic voice already reads
it as "kay-joo", and rewriting it to a Latin respelling would make the Arabic
voice code-switch mid-word.

## Attaching it

Per the docs, tables attach to an **agent version**, not the other way round,
and the draft `behavior` object is replaced wholesale — so resend the other
behavior blocks with it.

In the UI: Agents → *agent* → **SAFETY & QUALITY** → **PRONUNCIATION** → select
**Kejue Brand Pronunciation** → Save → promote → deploy. Do this for both
`kejue_outbound_english` and `kejue_outbound_arabic`.

Then **detach and archive the old `cadway-pronunciation` table**. Leaving it
attached ahead of this one would let `Cadway → Keh-jway` win on precedence,
since all entries from the first table run before any from the second.
Deleting it while still attached returns `409 pronunciation_table_in_use`.

## API form

```json
{
  "slug": "kejue-brand-pronunciation",
  "name": "Kejue Brand Pronunciation",
  "description": "Forces the correct two-syllable reading of the Kejue brand name and absorbs LLM/STT misspellings.",
  "entries": [
    { "from": "Kejue's",  "to": "kay-joos", "match": "word" },
    { "from": "Kejue",    "to": "kay-joo",  "match": "word" },
    { "from": "Kejwayh",  "to": "kay-joo",  "match": "word" },
    { "from": "Kejway",   "to": "kay-joo",  "match": "word" },
    { "from": "Keh-jway", "to": "kay-joo",  "match": "anywhere" },
    { "from": "Kedway",   "to": "kay-joo",  "match": "word" },
    { "from": "Cadway",   "to": "kay-joo",  "match": "word" },
    { "from": "Kajue",    "to": "kay-joo",  "match": "word" },
    { "from": "Kejoo",    "to": "kay-joo",  "match": "word" },
    { "from": "Keju",     "to": "kay-joo",  "match": "word" },
    { "from": "K-Jue",    "to": "kay-joo",  "match": "anywhere" },
    { "from": "كيجواي",   "to": "كيجو",     "match": "word" },
    { "from": "كيجوايه",  "to": "كيجو",     "match": "word" },
    { "from": "كيجوي",    "to": "كيجو",     "match": "word" },
    { "from": "كادواي",   "to": "كيجو",     "match": "word" },
    { "from": "كيدواي",   "to": "كيجو",     "match": "word" }
  ]
}
```

## Verifying it, and the one thing to check by ear

Respelling is voice-dependent — it is a hint to a specific TTS model, not a
phonetic standard. So confirm by ear on a test call rather than assuming:

1. Place a test call on the English agent and listen to the first sentence,
   where the brand name always appears.
2. If `kay-joo` comes out with a drawled or diphthongised first syllable, try
   these in order, changing only entry 1 and 2:
   - `kay joo` (space instead of hyphen — often the cleanest)
   - `KAY-joo` (capitals sometimes shift stress)
   - `kei-joo`
3. Repeat on the Arabic agent and confirm it says **كيجو**, not كيجواي.

Also worth a listen, since the table only rewrites TTS and not the transcript:
the dashboard and `call_summary` will still show "Kejue" spelled normally. That
is correct and should not be "fixed".
