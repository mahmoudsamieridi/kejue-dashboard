"""
Kejue outbound dashboard + the HTTP tools the voice agents call mid-conversation.

Availability is data, not code. A slot exists because somebody created it on
the dashboard; the agents can only ever offer what is in the `slots` table.
There is no hard-coded working day, no fixed hours and no weekend rule - if
you do not open a slot, no agent can book it.

Routes
    /                        dashboard
    /health                  uptime probe

    /api/slots               GET    list slots
    /api/slots               POST   open one or more slots
    /api/slots/<id>          DELETE remove a slot (open or booked)

    /webhook                 Sonus call.completed receiver
    /get_all_calls           dashboard backfill

    /check_availability      TOOL  check_available_times
    /book_time               TOOL  book_meeting_time

The two tool routes always answer 200 with a `status` field. Sonus does not
retry 4xx, and an agent that receives a bare error mid-call has nothing to say
to the prospect - so a refusal is modelled as data ("no_slots_on_date", plus
alternatives the agent can offer) rather than as an HTTP failure.
"""

import os
import sqlite3
from datetime import timedelta

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO

import scheduling as sch

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# Overridable so the test suite never writes to the live calendar.
DB_PATH = os.environ.get("KEJUE_DB", "kejue.db")

MAX_BULK_SLOTS = 50


# --------------------------------------------------------------------------
# database
# --------------------------------------------------------------------------

def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS calls (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     name TEXT, company TEXT, use_case TEXT,
                     summary TEXT, status TEXT)""")

    # One row per offerable slot. status is 'open' or 'booked'.
    c.execute("""CREATE TABLE IF NOT EXISTS slots (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     slot_date TEXT NOT NULL,
                     slot_time TEXT NOT NULL,
                     status TEXT NOT NULL DEFAULT 'open',
                     prospect_name TEXT,
                     company_name TEXT,
                     created_at TEXT,
                     booked_at TEXT)""")
    c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_slot_unique
                 ON slots(slot_date, slot_time)""")

    # The old booked_times table encoded a fixed grid of hours with no date.
    # Availability is dashboard-owned now, so it is dropped rather than
    # migrated - there is no sensible date to migrate those rows onto.
    c.execute("DROP TABLE IF EXISTS booked_times")

    conn.commit()
    conn.close()


init_db()


def row_to_slot(r, lang="en"):
    d = sch.parse_date(r["slot_date"])
    return {
        "id": r["id"],
        "date": r["slot_date"],
        "date_spoken": sch.spoken_date(d, lang),
        "time_slot": r["slot_time"],
        "time_spoken": sch.spoken_time(r["slot_time"], lang),
        "status": r["status"],
        "prospect_name": r["prospect_name"],
        "company_name": r["company_name"],
    }


def open_slots_on(cursor, d):
    """Open, still-attendable slots on a given date, earliest first."""
    rows = cursor.execute(
        "SELECT slot_time FROM slots WHERE slot_date = ? AND status = 'open' "
        "ORDER BY slot_time", (d.isoformat(),)).fetchall()
    return [r["slot_time"] for r in rows
            if sch.slot_is_bookable(d, r["slot_time"])]


def next_days_with_slots(cursor, start, count, lang="en"):
    """The next `count` dates at or after `start` that actually have slots."""
    rows = cursor.execute(
        "SELECT DISTINCT slot_date FROM slots WHERE status = 'open' "
        "AND slot_date >= ? AND slot_date <= ? ORDER BY slot_date",
        (start.isoformat(),
         (sch.today() + timedelta(days=sch.SEARCH_HORIZON_DAYS)).isoformat())
    ).fetchall()

    out = []
    for r in rows:
        d = sch.parse_date(r["slot_date"])
        times = open_slots_on(cursor, d)
        if times:
            out.append(describe_day(d, times, lang))
        if len(out) == count:
            break
    return out


def describe_day(d, times, lang):
    return {
        "date": d.isoformat(),
        "date_spoken": sch.spoken_date(d, lang),
        "slots": [{"time_slot": t, "spoken": sch.spoken_time(t, lang)}
                  for t in times],
        "slots_spoken": [sch.spoken_time(t, lang) for t in times],
    }


# --------------------------------------------------------------------------
# tool payload helpers
# --------------------------------------------------------------------------

def tool_args():
    """Sonus sends tool params as a JSON body; allow query args for manual curl."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    merged = dict(request.args)
    merged.update({k: v for k, v in data.items() if v is not None})
    return merged


def wants_arabic(args):
    lang = str(args.get("language") or args.get("lang") or "").lower()
    return lang.startswith("ar")


# --------------------------------------------------------------------------
# dashboard
# --------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    conn = db()
    n = conn.execute("SELECT COUNT(*) FROM slots WHERE status = 'open'").fetchone()[0]
    conn.close()
    return jsonify({"status": "ok", "today": sch.today().isoformat(),
                    "open_slots": n}), 200


@app.route("/get_all_calls", methods=["GET"])
def get_all_calls():
    conn = db()
    rows = conn.execute("SELECT name, company, use_case, summary, status "
                        "FROM calls ORDER BY id ASC").fetchall()
    conn.close()
    return jsonify([{
        "prospect_name": r["name"],
        "company_name": r["company"],
        "use_case": r["use_case"],
        "call_summary": r["summary"],
        "is_meeting_booked": r["status"] == "Meeting Booked",
    } for r in rows])


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}

    payload = {}
    extractions = (data.get("call") or {}).get("extractions") or []
    if extractions:
        payload = extractions[0].get("result") or {}

    name = payload.get("prospect_name") or "Unknown Prospect"
    company = payload.get("company_name") or "Unknown Company"
    use_case = payload.get("use_case") or "N/A"
    summary = payload.get("call_summary") or "No summary available."
    booked = str(payload.get("is_meeting_booked", False)).lower() == "true"
    status = "Meeting Booked" if booked else "Call Completed"

    conn = db()
    conn.execute("INSERT INTO calls (name, company, use_case, summary, status) "
                 "VALUES (?, ?, ?, ?, ?)",
                 (name, company, use_case, summary, status))
    conn.commit()
    conn.close()

    socketio.emit("new_call", payload)
    return "OK", 200


# --------------------------------------------------------------------------
# slot management - the dashboard owns availability
# --------------------------------------------------------------------------

@app.route("/api/slots", methods=["GET"])
def list_slots():
    """All slots from today onwards, plus a little context for the UI."""
    conn = db()
    rows = conn.execute(
        "SELECT * FROM slots WHERE slot_date >= ? "
        "ORDER BY slot_date, slot_time", (sch.today().isoformat(),)).fetchall()
    conn.close()

    slots = []
    for r in rows:
        try:
            s = row_to_slot(r)
        except sch.DateParseError:
            continue
        s["bookable"] = (s["status"] == "open"
                         and sch.slot_is_bookable(sch.parse_date(s["date"]),
                                                  s["time_slot"]))
        slots.append(s)

    return jsonify({
        "today": sch.today().isoformat(),
        "timezone": str(sch.BUSINESS_TZ),
        "slots": slots,
    })


@app.route("/api/slots", methods=["POST"])
def create_slots():
    """
    Open one or more slots.

        {"date": "2026-11-17", "times": ["10:00", "11:30"]}
        {"date": "next tuesday", "time": "2 pm"}

    Times are parsed leniently, so the UI can send whatever the user typed.
    Already-existing slots are reported rather than treated as errors - adding
    a day twice should be harmless.
    """
    data = request.get_json(silent=True) or {}

    try:
        d = sch.parse_date(data.get("date"))
    except sch.DateParseError:
        return jsonify({"error": "unclear_date",
                        "message": "Could not understand that date."}), 400

    raw_times = data.get("times")
    if raw_times is None:
        raw_times = [data.get("time")] if data.get("time") else []
    if not isinstance(raw_times, list) or not raw_times:
        return jsonify({"error": "no_times",
                        "message": "Give at least one time."}), 400
    if len(raw_times) > MAX_BULK_SLOTS:
        return jsonify({"error": "too_many",
                        "message": f"At most {MAX_BULK_SLOTS} slots at once."}), 400

    parsed, unclear = [], []
    for raw in raw_times:
        t = sch.parse_slot(raw)
        (parsed if t else unclear).append(t or raw)

    conn = db()
    c = conn.cursor()
    created, duplicates = [], []
    for t in sorted(set(parsed)):
        try:
            c.execute("INSERT INTO slots (slot_date, slot_time, status, created_at) "
                      "VALUES (?, ?, 'open', ?)",
                      (d.isoformat(), t, sch.now().isoformat(timespec="seconds")))
            created.append(t)
        except sqlite3.IntegrityError:
            duplicates.append(t)
    conn.commit()
    conn.close()

    return jsonify({
        "date": d.isoformat(),
        "date_spoken": sch.spoken_date(d),
        "created": created,
        "duplicates": duplicates,
        "unclear": unclear,
    }), 201 if created else 200


@app.route("/api/slots/<int:slot_id>", methods=["DELETE"])
def delete_slot(slot_id):
    """
    Remove a slot. Booked slots can be removed too - that is how a meeting is
    cancelled - so the response says what was actually deleted and the UI can
    warn before calling it.
    """
    conn = db()
    c = conn.cursor()
    row = c.execute("SELECT * FROM slots WHERE id = ?", (slot_id,)).fetchone()
    if row is None:
        conn.close()
        return jsonify({"error": "not_found"}), 404

    deleted = row_to_slot(row)
    c.execute("DELETE FROM slots WHERE id = ?", (slot_id,))
    conn.commit()
    conn.close()
    return jsonify({"deleted": deleted}), 200


# --------------------------------------------------------------------------
# TOOL: check_available_times
# --------------------------------------------------------------------------

@app.route("/check_availability", methods=["GET", "POST"])
def check_availability():
    """
    Optional `date` - the prospect's own phrasing. Omit it for the soonest days
    that have slots. Only ever returns slots that exist in the database.
    """
    args = tool_args()
    lang = "ar" if wants_arabic(args) else "en"
    raw_date = args.get("date") or args.get("preferred_date") or args.get("day")

    conn = db()
    cursor = conn.cursor()
    today = sch.today()

    response = {
        "status": "ok",
        "today": today.isoformat(),
        "today_spoken": sch.spoken_date(today, lang),
        "timezone": str(sch.BUSINESS_TZ),
    }

    def finish(extra, code=200):
        conn.close()
        response.update(extra)
        return jsonify(response), code

    # No date given: offer the soonest days that have anything.
    if not raw_date:
        days = next_days_with_slots(cursor, today, 3, lang)
        return finish({
            "requested_date": None,
            "days": days,
            "instruction": (
                "Offer these days and times and ask which suits the prospect. "
                "Speak the spoken forms, never the digits. They may also name "
                "another day - call this tool again with that date to check it."
                if days else
                "There is no availability on the calendar at all. Do not invent "
                "a time. Offer to have a specialist follow up by email instead, "
                "or agree a callback."
            ),
        })

    try:
        requested = sch.parse_date(raw_date)
    except sch.DateParseError:
        return finish({
            "status": "unclear_date",
            "requested_date": None,
            "requested_date_raw": str(raw_date),
            "days": next_days_with_slots(cursor, today, 3, lang),
            "instruction": ("You could not be understood. Ask the prospect to "
                            "say the day plainly, for example the weekday and "
                            "the month. Meanwhile you may offer these days."),
        })

    response["requested_date"] = requested.isoformat()
    response["requested_date_spoken"] = sch.spoken_date(requested, lang)

    if requested < today:
        return finish({
            "status": "unavailable",
            "reason": "in_the_past",
            "days": next_days_with_slots(cursor, today, 3, lang),
            "instruction": ("That date has already passed. Apologise lightly "
                            "and offer the days listed here."),
        })

    times = open_slots_on(cursor, requested)
    if times:
        return finish({
            "status": "available",
            "days": [describe_day(requested, times, lang)],
            "instruction": (
                "Tell the prospect which times are open on their requested day "
                "and ask which they want. Offer at most two or three. Speak the "
                "spoken forms, never the digits. Then call book_meeting_time."
            ),
        })

    days = next_days_with_slots(cursor, requested + timedelta(days=1), 3, lang)
    return finish({
        "status": "no_slots_on_date",
        "reason": "no_slots_on_date",
        "days": days,
        "instruction": (
            "Nothing is open on that day. Say so warmly - do not explain why - "
            "and offer the nearest days listed here."
            if days else
            "Nothing is open on that day and nothing later either. Do not invent "
            "a time. Offer a callback or an email follow-up instead."
        ),
    })


# --------------------------------------------------------------------------
# TOOL: book_meeting_time
# --------------------------------------------------------------------------

@app.route("/book_time", methods=["GET", "POST"])
def book_time():
    """
    Params: `date` and `time_slot` required, `prospect_name` / `company_name`
    optional. Books only a slot that exists and is still open - the booking is
    a conditional UPDATE, so two agents racing for the last slot cannot both win.
    """
    args = tool_args()
    lang = "ar" if wants_arabic(args) else "en"
    raw_date = args.get("date") or args.get("meeting_date") or args.get("day")
    raw_slot = args.get("time_slot") or args.get("time") or args.get("slot")

    conn = db()
    cursor = conn.cursor()

    def fail(status, instruction, extra=None, search_from=None):
        days = next_days_with_slots(cursor, search_from or sch.today(), 3, lang)
        conn.close()
        body = {"status": status, "booked": False,
                "days": days, "instruction": instruction}
        body.update(extra or {})
        return jsonify(body), 200

    if not raw_date:
        return fail("missing_date", "You did not supply a date. Ask which day "
                                    "they want, then call this tool again.")
    if not raw_slot:
        return fail("missing_time", "You did not supply a time. Ask which of "
                                    "the open times they want, then call again.")

    try:
        meeting_date = sch.parse_date(raw_date)
    except sch.DateParseError:
        return fail("unclear_date",
                    "The date could not be understood. Ask the prospect to say "
                    "the weekday and month plainly.",
                    {"requested_date_raw": str(raw_date)})

    slot = sch.parse_slot(raw_slot)
    if slot is None:
        return fail("unclear_time",
                    "That time could not be understood. Offer the open times "
                    "listed here and ask them to pick one.",
                    {"requested_time_raw": str(raw_slot)},
                    search_from=meeting_date)

    if meeting_date < sch.today():
        return fail("unavailable", "That date has passed, so nothing was "
                                   "booked. Offer the days listed here.",
                    {"reason": "in_the_past"})

    row = cursor.execute(
        "SELECT * FROM slots WHERE slot_date = ? AND slot_time = ?",
        (meeting_date.isoformat(), slot)).fetchone()

    if row is None:
        return fail("slot_not_offered",
                    "That time is not on the calendar, so nothing was booked. "
                    "Do not invent a time - offer only the times listed here.",
                    {"reason": "slot_not_offered"}, search_from=meeting_date)

    if row["status"] != "open":
        return fail("slot_taken",
                    "That slot was taken already. Apologise briefly and offer "
                    "the nearest open times listed here.",
                    {"reason": "slot_taken"}, search_from=meeting_date)

    if not sch.slot_is_bookable(meeting_date, slot):
        return fail("too_soon",
                    "That time is too close to now to be booked. Meetings need "
                    "at least an hour's notice. Offer the times listed here.",
                    {"reason": "too_soon"}, search_from=meeting_date)

    # Conditional update: only succeeds if the slot is still open.
    cursor.execute(
        "UPDATE slots SET status = 'booked', prospect_name = ?, "
        "company_name = ?, booked_at = ? WHERE id = ? AND status = 'open'",
        (args.get("prospect_name"), args.get("company_name"),
         sch.now().isoformat(timespec="seconds"), row["id"]))
    if cursor.rowcount == 0:
        return fail("slot_taken",
                    "That slot was taken a moment ago. Apologise briefly and "
                    "offer the nearest open times listed here.",
                    {"reason": "slot_taken"}, search_from=meeting_date)
    conn.commit()
    conn.close()

    spoken = sch.spoken_slot(meeting_date, slot, lang)
    socketio.emit("slot_booked", {"date": meeting_date.isoformat(),
                                  "time_slot": slot})

    return jsonify({
        "status": "booked",
        "booked": True,
        "date": meeting_date.isoformat(),
        "time_slot": slot,
        "confirmation_spoken": spoken,
        "instruction": (
            f"The meeting is confirmed. Read this back to the prospect exactly "
            f"as words: \"{spoken}\". Do not speak any digits. Then say goodbye "
            f"and trigger end_call with the reason \"completed\"."
        ),
    }), 200


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5001, debug=True,
                 allow_unsafe_werkzeug=True)
