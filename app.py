"""
Kejue outbound dashboard + the HTTP tools the voice agents call mid-conversation.

Routes
    /                        live dashboard
    /webhook                 Sonus call.completed receiver
    /get_all_calls           dashboard backfill
    /get_meetings            booked meetings, for the dashboard calendar panel
    /check_availability      TOOL  check_available_times
    /book_time               TOOL  book_meeting_time
    /health                  uptime probe (Render spins the free tier down)

The two tool routes always answer 200 with a `status` field. Sonus does not
retry 4xx, and an agent that receives a bare error mid-call has nothing to say
to the prospect - so a refusal is modelled as data ("unavailable", plus
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

    c.execute("""CREATE TABLE IF NOT EXISTS booked_times (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     time_slot TEXT)""")

    # Migration: the original table only held a time, which is exactly why the
    # agents could only ever book "tomorrow". Add the date and the lead it
    # belongs to, then backfill legacy rows onto the next working day.
    existing = {r["name"] for r in c.execute("PRAGMA table_info(booked_times)")}
    for column, ddl in (
        ("meeting_date", "ALTER TABLE booked_times ADD COLUMN meeting_date TEXT"),
        ("prospect_name", "ALTER TABLE booked_times ADD COLUMN prospect_name TEXT"),
        ("company_name", "ALTER TABLE booked_times ADD COLUMN company_name TEXT"),
        ("created_at", "ALTER TABLE booked_times ADD COLUMN created_at TEXT"),
    ):
        if column not in existing:
            c.execute(ddl)

    fallback = (sch.next_working_days(sch.today() + timedelta(days=1), 1)
                or [sch.today()])[0]
    c.execute("UPDATE booked_times SET meeting_date = ? WHERE meeting_date IS NULL",
              (fallback.isoformat(),))

    # Legacy rows stored the slot as "02:00 PM". Left alone those values flow
    # straight into the *_spoken fields, which is how digits end up being read
    # out on a call. Normalise them to "HH:MM", and drop the ones that cannot
    # be normalised or that collide once normalised - they are leftovers from
    # the old single-day design and carry no real date.
    seen = set()
    for row in c.execute("SELECT id, meeting_date, time_slot "
                         "FROM booked_times ORDER BY id").fetchall():
        slot = sch.parse_slot(row["time_slot"])
        key = (row["meeting_date"], slot)
        if slot is None or key in seen:
            c.execute("DELETE FROM booked_times WHERE id = ?", (row["id"],))
            continue
        seen.add(key)
        if slot != row["time_slot"]:
            c.execute("UPDATE booked_times SET time_slot = ? WHERE id = ?",
                      (slot, row["id"]))

    # One meeting per slot per day. This is the real double-booking guard;
    # the check in book_time() is only there to produce a friendly answer.
    c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_slot_unique
                 ON booked_times(meeting_date, time_slot)""")

    conn.commit()
    conn.close()


init_db()


def booked_on(cursor, d):
    rows = cursor.execute(
        "SELECT time_slot FROM booked_times WHERE meeting_date = ?",
        (d.isoformat(),)).fetchall()
    return {r["time_slot"] for r in rows}


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


def describe_day(d, booked, lang):
    """One day's availability, in both machine and spoken form."""
    slots = sch.free_slots(d, booked)
    return {
        "date": d.isoformat(),
        "date_spoken": sch.spoken_date(d, lang),
        "slots": [{"time_slot": s, "spoken": sch.spoken_time(s, lang)}
                  for s in slots],
        "slots_spoken": [sch.spoken_time(s, lang) for s in slots],
    }


# --------------------------------------------------------------------------
# dashboard
# --------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "today": sch.today().isoformat()}), 200


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


@app.route("/get_meetings", methods=["GET"])
def get_meetings():
    conn = db()
    rows = conn.execute(
        "SELECT meeting_date, time_slot, prospect_name, company_name "
        "FROM booked_times WHERE meeting_date >= ? "
        "ORDER BY meeting_date ASC, time_slot ASC",
        (sch.today().isoformat(),)).fetchall()
    conn.close()

    out = []
    for r in rows:
        try:
            d = sch.parse_date(r["meeting_date"])
        except sch.DateParseError:
            continue
        out.append({
            "date": r["meeting_date"],
            "date_spoken": sch.spoken_date(d),
            "time_slot": r["time_slot"],
            "time_spoken": sch.spoken_time(r["time_slot"]),
            "prospect_name": r["prospect_name"] or "—",
            "company_name": r["company_name"] or "—",
        })
    return jsonify(out)


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
# TOOL: check_available_times
# --------------------------------------------------------------------------

@app.route("/check_availability", methods=["GET", "POST"])
def check_availability():
    """
    Optional param `date` - any phrasing the prospect used ("next Tuesday",
    "2026-11-03", "بعد بكرا"). Omit it to get the soonest open days.

    Always returns `days[]`. When the requested date cannot host a meeting the
    response says why and offers the nearest alternatives, so the agent never
    has to improvise a calendar it cannot see.
    """
    args = tool_args()
    lang = "ar" if wants_arabic(args) else "en"
    raw_date = args.get("date") or args.get("preferred_date") or args.get("day")

    conn = db()
    cursor = conn.cursor()

    def suggest(start, count=3):
        out = []
        for d in sch.next_working_days(start, count + 4):
            day = describe_day(d, booked_on(cursor, d), lang)
            if day["slots"]:
                out.append(day)
            if len(out) == count:
                break
        return out

    today = sch.today()
    response = {
        "status": "ok",
        "today": today.isoformat(),
        "today_spoken": sch.spoken_date(today, lang),
        "timezone": "Asia/Amman",
        "booking_horizon_days": sch.MAX_HORIZON_DAYS,
    }

    # No date given: offer the soonest days with room.
    if not raw_date:
        days = suggest(today, 3)
        response["requested_date"] = None
        response["days"] = days
        response["instruction"] = (
            "Offer these days and times to the prospect and ask which suits them. "
            "Speak the spoken forms, never the digits. The prospect may also name "
            "any other working day within the booking horizon - call this tool "
            "again with that date to check it."
            if days else
            "There is no availability in the horizon. Offer to have a specialist "
            "email the prospect instead."
        )
        conn.close()
        return jsonify(response), 200

    # Date given: resolve it, then judge it.
    try:
        requested = sch.parse_date(raw_date)
    except sch.DateParseError:
        days = suggest(today, 3)
        conn.close()
        response.update({
            "status": "unclear_date",
            "requested_date": None,
            "requested_date_raw": str(raw_date),
            "days": days,
            "instruction": (
                "You could not be understood. Ask the prospect to say the day "
                "plainly, for example the weekday and the month. Meanwhile you "
                "may offer the days listed here."
            ),
        })
        return jsonify(response), 200

    response["requested_date"] = requested.isoformat()
    response["requested_date_spoken"] = sch.spoken_date(requested, lang)

    ok, reason = sch.classify_date(requested)
    if not ok:
        search_from = today if reason in ("in_the_past", "weekend") else today
        if reason == "weekend":
            search_from = requested
        days = suggest(search_from, 3)
        messages = {
            "in_the_past": "That date has already passed. Apologise lightly and "
                           "offer the days listed here.",
            "weekend": "The team does not take meetings on Friday or Saturday. "
                       "Say so warmly and offer the days listed here.",
            "beyond_horizon": "That is further out than the calendar goes. Say you "
                              "can book up to about two months ahead and offer "
                              "the days listed here.",
        }
        conn.close()
        response.update({
            "status": "unavailable",
            "reason": reason,
            "days": days,
            "instruction": messages[reason],
        })
        return jsonify(response), 200

    day = describe_day(requested, booked_on(cursor, requested), lang)
    if day["slots"]:
        conn.close()
        response.update({
            "status": "available",
            "days": [day],
            "instruction": (
                "Tell the prospect which times are open on their requested day "
                "and ask which one they want. Speak the spoken forms, never the "
                "digits. Then call book_meeting_time with the date and time_slot."
            ),
        })
        return jsonify(response), 200

    days = suggest(requested + timedelta(days=1), 3)
    conn.close()
    response.update({
        "status": "day_full",
        "reason": "fully_booked",
        "days": days,
        "instruction": ("That day has nothing left. Say it is fully booked and "
                        "offer the nearest days listed here."),
    })
    return jsonify(response), 200


# --------------------------------------------------------------------------
# TOOL: book_meeting_time
# --------------------------------------------------------------------------

@app.route("/book_time", methods=["GET", "POST"])
def book_time():
    """
    Params: `date` (required), `time_slot` (required), plus optional
    `prospect_name` / `company_name` for the dashboard.

    Re-validates everything check_availability validated. The prospect may have
    changed their mind, another agent may have taken the slot in between, or the
    model may have invented a time - none of which should produce a booking.
    """
    args = tool_args()
    lang = "ar" if wants_arabic(args) else "en"
    raw_date = args.get("date") or args.get("meeting_date") or args.get("day")
    raw_slot = args.get("time_slot") or args.get("time") or args.get("slot")

    conn = db()
    cursor = conn.cursor()

    def fail(status, instruction, extra=None, search_from=None):
        days = []
        for d in sch.next_working_days(search_from or sch.today(), 7):
            day = describe_day(d, booked_on(cursor, d), lang)
            if day["slots"]:
                days.append(day)
            if len(days) == 3:
                break
        conn.close()
        body = {"status": status, "booked": False,
                "days": days, "instruction": instruction}
        body.update(extra or {})
        return jsonify(body), 200

    if not raw_date:
        return fail("missing_date",
                    "You did not supply a date. Ask the prospect which day they "
                    "want, then call this tool again.")
    if not raw_slot:
        return fail("missing_time",
                    "You did not supply a time. Ask the prospect which of the "
                    "open times they want, then call this tool again.")

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
                    "That is not one of the bookable times. Meetings start on "
                    "the hour between ten in the morning and five in the "
                    "afternoon. Offer the open times listed here.",
                    {"requested_time_raw": str(raw_slot)},
                    search_from=meeting_date)

    ok, reason = sch.classify_date(meeting_date)
    if not ok:
        messages = {
            "in_the_past": "That date has passed, so nothing was booked. "
                           "Offer the days listed here.",
            "weekend": "Friday and Saturday are the weekend, so nothing was "
                       "booked. Offer the days listed here.",
            "beyond_horizon": "That is beyond the booking horizon, so nothing "
                              "was booked. Offer the days listed here.",
        }
        return fail("unavailable", messages[reason], {"reason": reason},
                    search_from=meeting_date if reason == "weekend" else None)

    if not sch.slot_is_bookable(meeting_date, slot):
        return fail("too_soon",
                    "That time is too close to now to be booked. Meetings need "
                    "at least an hour's notice. Offer the times listed here.",
                    {"reason": "too_soon"}, search_from=meeting_date)

    try:
        cursor.execute(
            "INSERT INTO booked_times (meeting_date, time_slot, prospect_name, "
            "company_name, created_at) VALUES (?, ?, ?, ?, ?)",
            (meeting_date.isoformat(), slot,
             args.get("prospect_name"), args.get("company_name"),
             sch.now().isoformat(timespec="seconds")))
        conn.commit()
    except sqlite3.IntegrityError:
        # The unique index caught a race or a repeat call.
        return fail("slot_taken",
                    "That slot was taken a moment ago. Apologise briefly and "
                    "offer the nearest open times listed here.",
                    {"reason": "slot_taken"}, search_from=meeting_date)

    spoken = sch.spoken_slot(meeting_date, slot, lang)
    conn.close()

    socketio.emit("new_meeting", {
        "date": meeting_date.isoformat(),
        "time_slot": slot,
        "prospect_name": args.get("prospect_name"),
        "company_name": args.get("company_name"),
    })

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
