# Kejue outbound voice agents + dashboard

Two Sonus outbound sales agents (English and Jordanian Arabic) that qualify a
prospect and book a meeting on a real calendar, plus a Flask dashboard that
receives the post-call webhook and serves the two HTTP tools the agents call
mid-conversation.

```
  Sonus agent ──(in-call)──> /check_availability ─┐
       │                     /book_time          ├── SQLite ── dashboard
       └──(call.completed)──> /webhook ───────────┘
```

---

## What changed in this round

Addressing the five points of review feedback.

### 1. Brand pronunciation

The attached table still keyed on the old placeholder name — `Cadway →
Keh-jway` — so the literal string **Kejue** that the agents write matched no
entry and the voice read the raw spelling, producing "ke-JOO-ay". The
replacement target was also wrong, which is where the Arabic agent's
**كيجواي / "Kejwayh"** came from.

Replaced with `kejue-brand-pronunciation`: **Kejue → kay-joo**, plus sixteen
entries absorbing the misspellings both models actually produce, in both
scripts. Full table, ordering rationale, API payload, and the by-ear
verification steps: **[`prompts/pronunciation_table.md`](prompts/pronunciation_table.md)**.

The old `cadway-pronunciation` table must be **detached and archived** — if it
stays attached ahead of the new one it wins on precedence.

### 2. Too eager to book

The old prompt went greeting → one question → pitch → close. It booked on the
first "sounds good".

Added an explicit **qualification bar** (section 4, GOAL G) that blocks the
meeting proposal until three conditions hold: the agent knows what the company
does, it has a specific phone problem *in the prospect's own words*, and it has
asked at least three genuine discovery questions. It holds even when the
prospect pushes to book early, with a scripted way to agree and still qualify.

The prompt also now requires the agent to **decline to book** when there is no
phone operation, and frames that as a successful call — previously every path
led to a booking.

### 3. Booking any future date

This was a data-model problem, not a prompt problem. The old table was
`booked_times(id, time_slot)` — **a time with no date**. There was nowhere to
put a date, so "tomorrow" was the only thing the system could mean.

Rebuilt around a real calendar:

- `booked_times` gains `meeting_date`, `prospect_name`, `company_name`,
  `created_at`, with a `UNIQUE(meeting_date, time_slot)` index as the real
  double-booking guard.
- Any date within a **60-day horizon** is bookable.
- **Asia/Amman** throughout, so "tomorrow" means tomorrow for the sales team.
- Sunday–Thursday working week; Friday and Saturday are refused.
- Slots on the hour, ten to five, with a one-hour minimum lead time.
- Migration normalises legacy `"02:00 PM"` rows to `"14:00"`, drops
  unparseable and colliding ones, and backfills a date.

**The design decision worth calling out:** the agent is told *not to compute
dates*. It passes the prospect's phrasing through verbatim — `"next Tuesday"`,
`"بعد أسبوع"`, `"the third of November"` — and `scheduling.parse_date()`
resolves it server-side against the business timezone. LLMs are unreliable at
date arithmetic and there is no reason to let that unreliability reach a
calendar. The parser handles ISO, day-first numeric, relative words in both
languages, weekday names in both languages, "in N days/weeks", and month-name
forms, and **raises rather than guesses** when it cannot tell.

Both tools return **200 with a `status` field**, never a 4xx. Sonus does not
retry 4xx, and an agent handed a bare error mid-call has nothing to say to the
prospect. A refusal is modelled as data — a status, an `instruction` telling
the agent what to say, and concrete alternative days to offer.

Every date and time comes back in two forms: `"14:00"` for the machine and
`"two in the afternoon"` / `"تنتين بعد الضهر"` for the voice, so the no-digits
guardrail cannot be violated by reading a tool response aloud.

### 4. Prompt structure

Both prompts rewritten to the ten sections requested — identity and objectives,
guardrails, knowledge boundaries, flexible conversation goals, natural voice
mechanics, objection handling, tools, callback scheduling, pronunciation rules,
call-ending behaviour.

- **[`prompts/kejue_outbound_english.md`](prompts/kejue_outbound_english.md)**
- **[`prompts/kejue_outbound_arabic.md`](prompts/kejue_outbound_arabic.md)**

Notable choices:

- **Knowledge boundaries are a deny-list, not just an allow-list.** It names
  the things that must never be answered — price, certifications, named
  customers, latency, integrations — and gives the exact honest phrasing for
  "I don't know", because a confident wrong answer on pricing costs the deal.
- **Conversation goals, not a script.** Lettered goals reachable in any order,
  with the qualification bar gating the close.
- **Objection handling pairs every objection with a question back**, and caps
  at two attempts — a third push is a guardrail violation.
- **The Arabic prompt is written in English.** Instructing in English while
  requiring Arabic output follows directives more reliably and stops
  instruction text leaking into speech; every spoken line is given as an Arabic
  example. It also guards against dialect drift into Egyptian/Gulf/Fusha, and
  explicitly *permits* code-switching to English for technical terms, because
  that is how Jordanian professionals actually talk.
- **Callback scheduling is a real fallback goal**, not an afterthought — a busy
  prospect is not a no.
- **Call-ending is explicit** about when to end, how, and which `end_call`
  reason to use, because the common failure on a good call is one turn too many.

### 5. Testing

Two layers, because the earlier test calls were cooperative — short agreeable
replies that never made the agent work.

**Automated** — [`evals/kejue_outbound_eval_suite.json`](evals/kejue_outbound_eval_suite.json):
25 adversarial scenarios and 16 graded checks for the Sonus evals framework.
Personas are written as mystery-shopper briefs that *withhold* information.
Covers premature booking, no-fit businesses, pricing interrogation, prompt
injection, dates beyond the horizon, weekends, past dates, nonexistent times,
a prospect who changes their mind four times, digit-leak pressure, AI
challenges, hostility and do-not-call, gatekeepers, sensitive-data capture,
dialect drift, and mid-call code-switching. Eleven checks are **blocking**.

**Manual** — [`evals/manual_call_test_script.md`](evals/manual_call_test_script.md):
a twelve-call run sheet with exactly what to say, what to watch for, and a
results table.

**Backend** — `test_scheduling.py`, 68 assertions, all passing. This is the
layer that can be run here and now; the two above need a live Sonus workspace.

```
$ python test_scheduling.py
68 passed, 0 failed
```

It found one real bug during development: legacy `"02:00 PM"` rows flowed
straight into the `*_spoken` fields, which is precisely how digits end up being
read out on a call. Fixed in the migration, with a regression test.

---

## Running it

```bash
pip install -r requirements.txt
python app.py              # http://127.0.0.1:5001
python test_scheduling.py  # 68 assertions
```

`KEJUE_DB` overrides the database path; the test suite uses it so it can never
touch the live calendar.

Deployed on Render from this repo. `tzdata` is in `requirements.txt` on
purpose — `ZoneInfo("Asia/Amman")` raises `ZoneInfoNotFoundError` on slim Linux
images without it.

## Endpoints

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Dashboard |
| `/health` | GET | Uptime probe |
| `/webhook` | POST | Sonus `call.completed` receiver |
| `/get_all_calls` | GET | Dashboard backfill |
| `/get_meetings` | GET | Upcoming meetings panel |
| `/check_availability` | GET/POST | **Tool** `check_available_times` |
| `/book_time` | GET/POST | **Tool** `book_meeting_time` |

### `check_available_times`

Optional `date` (the prospect's own phrasing), optional `language` (`en`/`ar`).
Omit `date` for the soonest open days.

Returns `status` ∈ `ok` · `available` · `unavailable` · `day_full` ·
`unclear_date`, always with `days[]` and an `instruction` string.

### `book_meeting_time`

`date` and `time_slot` required; `prospect_name`, `company_name`, `language`
optional.

Returns `status` ∈ `booked` · `slot_taken` · `unavailable` · `too_soon` ·
`unclear_date` · `unclear_time` · `missing_date` · `missing_time`. On success,
`confirmation_spoken` is the digit-free line for the agent to read back.

---

## Sonus configuration checklist

- [ ] Both agents on the new prompts
- [ ] `kejue-brand-pronunciation` attached to both
- [ ] `cadway-pronunciation` **detached and archived**
- [ ] Both tools attached to both agents, pointed at the deployed URL
- [ ] `book_meeting_time` has dynamic params `date` **and** `time_slot`
      (`date` is new — the tool previously took only `time_slot`)
- [ ] `end_call` enabled, type `soft`
- [ ] Extraction object `Lead_Qualification_Data` attached post-call
- [ ] Webhook `crm-webhook` → `/webhook` on `call.completed`

## Known limitations

- SQLite on Render's free tier has an ephemeral disk; bookings do not survive a
  redeploy. Fine for a demo, needs Postgres or a real calendar API for
  production.
- The calendar is a fixed slot grid, not a Google/Outlook integration. The tool
  contract is deliberately shaped so swapping the backing store does not touch
  the prompts.
- No authentication on the tool endpoints. Sonus supports secret-injected
  Bearer headers; production should use one.
- Timezone is the sales team's, not the prospect's. A prospect abroad would be
  quoted Amman times.
