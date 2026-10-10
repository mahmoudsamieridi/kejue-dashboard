"""
Adversarial tests for the scheduling tools.

Run:  python test_scheduling.py

These are the cases a prospect actually creates on a live call: vague days,
weekends, dates in the past, dates a year out, times that do not exist, the
same slot twice, and the model passing a raw phrase straight through.
"""

import os
import sys
import tempfile
from datetime import timedelta

# Use a scratch database so a test run never touches real bookings.
_tmp = tempfile.mkdtemp()
os.environ["KEJUE_DB"] = os.path.join(_tmp, "test.db")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as application          # noqa: E402
import scheduling as sch           # noqa: E402

assert application.DB_PATH == os.environ["KEJUE_DB"], \
    "app must honour KEJUE_DB so tests cannot touch the live calendar"
client = application.app.test_client()

PASS = FAIL = 0
FAILURES = []


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL += 1
        FAILURES.append(f"{label} {detail}".strip())
        print(f"  FAIL  {label} {detail}")


def post(path, **payload):
    return client.post(path, json=payload).get_json()


def next_working(offset_from=None, skip=0):
    start = (offset_from or sch.today()) + timedelta(days=1)
    return sch.next_working_days(start, skip + 1)[skip]


# ---------------------------------------------------------------- date parsing
print("\n[1] date parsing")

today = sch.today()
check("ISO date", sch.parse_date("2026-11-03").isoformat() == "2026-11-03")
check("day-first numeric", sch.parse_date("03/11/2026").isoformat() == "2026-11-03")
check("today", sch.parse_date("today") == today)
check("tomorrow", sch.parse_date("tomorrow") == today + timedelta(days=1))
check("day after tomorrow",
      sch.parse_date("day after tomorrow") == today + timedelta(days=2))
check("arabic tomorrow", sch.parse_date("بكرا") == today + timedelta(days=1))
check("arabic day after", sch.parse_date("بعد بكرا") == today + timedelta(days=2))
check("in 3 days", sch.parse_date("in 3 days") == today + timedelta(days=3))
check("in a week", sch.parse_date("in a week") == today + timedelta(days=7))
# Regression: the unit word used to double as a count, so "بعد أسبوع" came
# out as seven WEEKS. Caught against the live service, not by the old suite.
for phrase, days in [("بعد أسبوع", 7), ("بعد اسبوع", 7), ("in one week", 7),
                     ("after a week", 7), ("بعد أسبوعين", 14),
                     ("in two weeks", 14), ("بعد 3 ايام", 3),
                     ("بعد يومين", 2), ("in 10 days", 10)]:
    got = sch.parse_date(phrase)
    check(f"{phrase!r} = +{days}d", got == today + timedelta(days=days),
          f"got +{(got - today).days}d")
check("weekday is always future",
      sch.parse_date("monday") > today and sch.parse_date("monday").weekday() == 0)
check("next tuesday is 8-14 days out",
      7 <= (sch.parse_date("next tuesday") - today).days <= 14)
check("arabic weekday",
      sch.parse_date("الخميس").weekday() == 3)
check("month name + day",
      sch.parse_date("october 14").month == 10 and sch.parse_date("october 14").day == 14)
check("bare month/day rolls to next year when past",
      sch.parse_date("january 5") > today)

check("yesterday", sch.parse_date("yesterday") == today - timedelta(days=1))
check("arabic yesterday", sch.parse_date("امبارح") == today - timedelta(days=1))
check("day before yesterday",
      sch.parse_date("day before yesterday") == today - timedelta(days=2))
check("last week", sch.parse_date("last week") == today - timedelta(days=7))
check("last tuesday is in the past",
      sch.parse_date("last tuesday") < today
      and sch.parse_date("last tuesday").weekday() == 1,
      str(sch.parse_date("last tuesday")))
check("next vs last tuesday differ",
      sch.parse_date("next tuesday") != sch.parse_date("last tuesday"))

for junk in ("whenever you like", "", "asap", "the usual time"):
    try:
        sch.parse_date(junk)
        check(f"gibberish rejected: {junk!r}", False, "parsed instead of raising")
    except sch.DateParseError:
        check(f"gibberish rejected: {junk!r}", True)


# ---------------------------------------------------------------- time parsing
print("\n[2] time parsing")

check('"10:00 AM"', sch.parse_slot("10:00 AM") == "10:00")
check('"10 am"', sch.parse_slot("10 am") == "10:00")
check('"2 pm"', sch.parse_slot("2 pm") == "14:00")
check('"14:00"', sch.parse_slot("14:00") == "14:00")
check('bare "2" assumed afternoon', sch.parse_slot("2") == "14:00")
check('"ten in the morning"', sch.parse_slot("ten in the morning") == "10:00")
check('"twelve noon"', sch.parse_slot("twelve noon") == "12:00")
check('arabic "خمسة بعد الضهر"', sch.parse_slot("خمسة بعد الضهر") == "17:00")
check("out-of-hours 9am rejected", sch.parse_slot("9:00 AM") is None)
check("out-of-hours 8pm rejected", sch.parse_slot("8 pm") is None)
check("half-hour rejected", sch.parse_slot("10:30 AM") is None)
check("nonsense rejected", sch.parse_slot("sometime soon") is None)


# -------------------------------------------------------------- spoken wording
print("\n[3] spoken forms contain no digits")

d = sch.parse_date("2026-11-03")
for lang in ("en", "ar"):
    text = sch.spoken_slot(d, "14:00", lang)
    check(f"{lang} slot wording digit-free: {text!r}",
          not any(ch.isdigit() for ch in text))
check("english wording reads naturally",
      sch.spoken_slot(d, "14:00", "en") ==
      "Tuesday the third of November at two in the afternoon",
      sch.spoken_slot(d, "14:00", "en"))


# --------------------------------------------------------- check_availability
print("\n[4] check_availability")

r = post("/check_availability")
check("no date -> offers days", r["status"] == "ok" and len(r["days"]) >= 1)
check("no date -> days are working days",
      all(sch.parse_date(x["date"]).weekday() not in sch.WEEKEND_DAYS
          for x in r["days"]))
check("no date -> exposes today", r["today"] == sch.today().isoformat())
check("no date -> spoken day has no digits",
      not any(ch.isdigit() for ch in r["days"][0]["date_spoken"]))

far = next_working(sch.today() + timedelta(days=20))
r = post("/check_availability", date=far.isoformat())
check("far future date is available (not just tomorrow!)",
      r["status"] == "available" and r["days"][0]["date"] == far.isoformat(),
      r.get("status"))

r = post("/check_availability", date="2020-01-01")
check("past date refused", r["status"] == "unavailable"
      and r["reason"] == "in_the_past", r.get("reason"))
check("past date still offers alternatives", len(r["days"]) >= 1)

friday = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
r = post("/check_availability", date=friday.isoformat())
check("friday refused as weekend",
      r["status"] == "unavailable" and r["reason"] == "weekend", r.get("reason"))

r = post("/check_availability", date=(today + timedelta(days=400)).isoformat())
check("beyond horizon refused",
      r["status"] == "unavailable" and r["reason"] == "beyond_horizon",
      r.get("reason"))

for phrase in ("yesterday", "last tuesday", "last week", "امبارح"):
    r = post("/check_availability", date=phrase)
    check(f"past phrase {phrase!r} -> in_the_past, not unclear_date",
          r["status"] == "unavailable" and r["reason"] == "in_the_past",
          f"{r.get('status')}/{r.get('reason')}")

r = post("/check_availability", date="whenever suits you")
check("unparseable date handled gracefully",
      r["status"] == "unclear_date" and len(r["days"]) >= 1, r.get("status"))

r = post("/check_availability", date="next tuesday", language="ar")
check("arabic wording returned",
      any(ord(ch) > 0x600 for ch in r["days"][0]["date_spoken"]))


# ------------------------------------------------------------------ book_time
print("\n[5] book_time")

target = next_working(sch.today() + timedelta(days=10))
r = post("/book_time", date=target.isoformat(), time_slot="11:00 AM",
         prospect_name="Mahmoud", company_name="Scanwave")
check("books a date ten+ days out", r["status"] == "booked" and r["booked"] is True,
      r.get("status"))
check("confirmation is digit-free",
      not any(ch.isdigit() for ch in r["confirmation_spoken"]),
      r.get("confirmation_spoken"))

r = post("/book_time", date=target.isoformat(), time_slot="11:00 AM")
check("double booking refused",
      r["status"] == "slot_taken" and r["booked"] is False, r.get("status"))
check("double booking offers alternatives", len(r["days"]) >= 1)

r = post("/check_availability", date=target.isoformat())
check("booked slot disappears from availability",
      "11:00" not in [s["time_slot"] for s in r["days"][0]["slots"]])

r = post("/book_time", date=target.isoformat(), time_slot="9:00 AM")
check("out-of-hours booking refused", r["status"] == "unclear_time", r.get("status"))

r = post("/book_time", date=friday.isoformat(), time_slot="11:00 AM")
check("weekend booking refused",
      r["status"] == "unavailable" and r["reason"] == "weekend", r.get("status"))

r = post("/book_time", date="2020-05-05", time_slot="11:00 AM")
check("past booking refused",
      r["status"] == "unavailable" and r["reason"] == "in_the_past", r.get("status"))

r = post("/book_time", time_slot="11:00 AM")
check("missing date refused", r["status"] == "missing_date", r.get("status"))

r = post("/book_time", date=target.isoformat())
check("missing time refused", r["status"] == "missing_time", r.get("status"))

r = post("/book_time", date="in 12 days", time_slot="three in the afternoon")
check("natural-language date + spoken time both accepted",
      r["status"] == "booked", r.get("status"))

r = post("/book_time", date=sch.today().isoformat(), time_slot="10:00 AM")
check("today's early slot refused as past/too soon",
      r["status"] in ("too_soon", "unclear_time", "unavailable", "booked"),
      r.get("status"))

r = post("/book_time", date=target.isoformat(), time_slot="sometime next week")
check("vague time refused rather than guessed",
      r["status"] == "unclear_time", r.get("status"))


# ------------------------------------------------------------------- dashboard
print("\n[6] dashboard wiring")

r = client.get("/get_meetings").get_json()
check("meetings endpoint lists bookings", len(r) >= 1)
check("meetings carry a real date", r[0]["date"] >= sch.today().isoformat())
check("meetings carry spoken wording",
      not any(ch.isdigit() for ch in r[0]["date_spoken"]))

r = client.post("/webhook", json={"call": {"extractions": [
    {"result": {"prospect_name": "Lina", "company_name": "Aqaba Logistics",
                "use_case": "Inbound support", "call_summary": "Interested.",
                "is_meeting_booked": True}}]}})
check("webhook accepts a call.completed payload", r.status_code == 200)
check("webhook survives an empty payload",
      client.post("/webhook", json={}).status_code == 200)
check("call appears in dashboard feed",
      any(c["prospect_name"] == "Lina"
          for c in client.get("/get_all_calls").get_json()))


# ------------------------------------------------------- legacy db migration
print("\n[7] legacy database migration")

# Rebuild the pre-date schema exactly as the original app.py left it: a bare
# time, in "02:00 PM" form, with no date column at all.
legacy_path = os.path.join(_tmp, "legacy.db")
import sqlite3  # noqa: E402

lc = sqlite3.connect(legacy_path)
lc.execute("CREATE TABLE booked_times (id INTEGER PRIMARY KEY AUTOINCREMENT, "
           "time_slot TEXT)")
for v in ["10:00 AM", "02:00 PM", "02:00 PM", "05:00 PM", "not a time"]:
    lc.execute("INSERT INTO booked_times (time_slot) VALUES (?)", (v,))
lc.commit()
lc.close()

application.DB_PATH = legacy_path
application.init_db()

conn = sqlite3.connect(legacy_path)
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT meeting_date, time_slot FROM booked_times").fetchall()
conn.close()

slots = [r["time_slot"] for r in rows]
check("legacy slots normalised to 24h", sorted(slots) == ["10:00", "14:00", "17:00"],
      str(sorted(slots)))
check("legacy duplicate dropped", slots.count("14:00") == 1)
check("unparseable legacy slot dropped", "not a time" not in slots)
check("legacy rows got a date", all(r["meeting_date"] for r in rows))
check("no digits leak into spoken form after migration",
      all(not any(ch.isdigit() for ch in sch.spoken_time(s)) for s in slots),
      str([sch.spoken_time(s) for s in slots]))

application.DB_PATH = os.environ["KEJUE_DB"]


print(f"\n{'=' * 60}\n{PASS} passed, {FAIL} failed")
if FAILURES:
    print("\nFailures:")
    for f in FAILURES:
        print(f"  - {f}")
sys.exit(1 if FAIL else 0)
