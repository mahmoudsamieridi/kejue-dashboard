"""
Adversarial tests for the scheduling tools and the availability API.

Run:  python test_scheduling.py

Availability is dashboard-owned, so the central rule under test is: an agent
can book a slot if and only if somebody opened that exact slot, it is still
open, and it is far enough ahead to attend. Everything else must refuse and
offer real alternatives.
"""

import os
import sys
import tempfile
from datetime import timedelta

# Test labels contain Arabic. On a console defaulting to a legacy codepage
# (cp1252 on Windows) printing them raises UnicodeEncodeError and the whole
# run dies on output rather than on a real failure.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

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


def add_slots(d, times):
    return client.post("/api/slots",
                       json={"date": d.isoformat(), "times": times}).get_json()


def wipe():
    conn = application.db()
    conn.execute("DELETE FROM slots")
    conn.commit()
    conn.close()


today = sch.today()
D1 = today + timedelta(days=3)     # ordinary future day
D2 = today + timedelta(days=10)
D_FAR = today + timedelta(days=90)  # far future - must work, nothing is capped


# ---------------------------------------------------------------- date parsing
print("\n[1] date parsing")

check("ISO date", sch.parse_date("2026-11-03").isoformat() == "2026-11-03")
check("day-first numeric", sch.parse_date("03/11/2026").isoformat() == "2026-11-03")
check("today", sch.parse_date("today") == today)
check("tomorrow", sch.parse_date("tomorrow") == today + timedelta(days=1))
check("arabic tomorrow", sch.parse_date("بكرا") == today + timedelta(days=1))
check("day after tomorrow",
      sch.parse_date("day after tomorrow") == today + timedelta(days=2))
check("yesterday", sch.parse_date("yesterday") == today - timedelta(days=1))
check("arabic yesterday", sch.parse_date("امبارح") == today - timedelta(days=1))
check("last week", sch.parse_date("last week") == today - timedelta(days=7))
check("last tuesday in the past",
      sch.parse_date("last tuesday") < today
      and sch.parse_date("last tuesday").weekday() == 1)
check("weekday is always future",
      sch.parse_date("monday") > today and sch.parse_date("monday").weekday() == 0)
check("next tuesday is 8-14 days out",
      7 <= (sch.parse_date("next tuesday") - today).days <= 14)
check("arabic weekday", sch.parse_date("الخميس").weekday() == 3)
check("month name + day",
      sch.parse_date("october 14").month == 10
      and sch.parse_date("october 14").day == 14)

# Regression: the unit word used to double as a count, so "بعد أسبوع" came
# out as seven WEEKS.
for phrase, days in [("in 3 days", 3), ("in a week", 7), ("بعد أسبوع", 7),
                     ("بعد اسبوع", 7), ("in one week", 7), ("after a week", 7),
                     ("بعد أسبوعين", 14), ("in two weeks", 14),
                     ("بعد 3 ايام", 3), ("بعد يومين", 2), ("in 10 days", 10)]:
    got = sch.parse_date(phrase)
    check(f"{phrase!r} = +{days}d", got == today + timedelta(days=days),
          f"got +{(got - today).days}d")

for junk in ("whenever you like", "", "asap", "the usual time"):
    try:
        sch.parse_date(junk)
        check(f"gibberish rejected: {junk!r}", False, "parsed instead of raising")
    except sch.DateParseError:
        check(f"gibberish rejected: {junk!r}", True)


# ---------------------------------------------------------------- time parsing
print("\n[2] time parsing")

for raw, want in [("10:00 AM", "10:00"), ("10 am", "10:00"), ("2 pm", "14:00"),
                  ("14:00", "14:00"), ("2", "14:00"), ("09:30", "09:30"),
                  ("9:30 am", "09:30"), ("ten in the morning", "10:00"),
                  ("half past two", "14:30"), ("quarter past ten", "10:15"),
                  ("quarter to three", "14:45"), ("8 pm", "20:00"),
                  ("عشرة الصبح", "10:00"), ("تلاتة ونص", "15:30"),
                  ("خمسة بعد الضهر", "17:00"), ("عشرة وربع", "10:15")]:
    check(f"{raw!r} -> {want}", sch.parse_slot(raw) == want,
          f"got {sch.parse_slot(raw)}")

for junk in ("sometime soon", "", "whenever", "the usual"):
    check(f"unparseable time rejected: {junk!r}", sch.parse_slot(junk) is None)


# -------------------------------------------------------------- spoken wording
print("\n[3] spoken forms never contain digits")

for t in ["10:00", "12:00", "09:30", "14:15", "16:45", "17:20", "08:07",
          "13:37", "20:00", "23:55"]:
    for lang in ("en", "ar"):
        said = sch.spoken_time(t, lang)
        check(f"{lang} {t} -> {said!r}",
              not any(ch.isdigit() for ch in said))

d = sch.parse_date("2026-11-03")
check("english wording reads naturally",
      sch.spoken_slot(d, "14:30", "en") ==
      "Tuesday the third of November at half past two in the afternoon",
      sch.spoken_slot(d, "14:30", "en"))
check("noon is special-cased", sch.spoken_time("12:00", "en") == "twelve noon")
check("arabic date digit-free",
      not any(ch.isdigit() for ch in sch.spoken_date(d, "ar")))


# ------------------------------------------------------------ availability API
print("\n[4] availability API (dashboard owns the calendar)")

wipe()

r = client.get("/api/slots").get_json()
check("starts empty", r["slots"] == [])

r = add_slots(D1, ["10:00", "11:00", "2 pm"])
check("creates slots from mixed formats", sorted(r["created"]) == ["10:00", "11:00", "14:00"],
      str(r))
check("no unclear times", r["unclear"] == [])

r = add_slots(D1, ["10:00"])
check("duplicate reported, not errored", r["duplicates"] == ["10:00"] and r["created"] == [])

r = add_slots(D1, ["not a time"])
check("unparseable time reported", r["unclear"] == ["not a time"])

r = client.post("/api/slots", json={"date": "gibberish", "times": ["10:00"]})
check("bad date rejected with 400", r.status_code == 400)

r = client.post("/api/slots", json={"date": D1.isoformat(), "times": []})
check("empty times rejected with 400", r.status_code == 400)

r = client.get("/api/slots").get_json()
check("lists what was created", len(r["slots"]) == 3)
check("slots carry spoken wording",
      all(not any(ch.isdigit() for ch in s["time_spoken"]) for s in r["slots"]))

slot_id = r["slots"][0]["id"]
res = client.delete(f"/api/slots/{slot_id}")
check("deletes a slot", res.status_code == 200)
check("deletion is reflected", len(client.get("/api/slots").get_json()["slots"]) == 2)
check("deleting twice is 404", client.delete(f"/api/slots/{slot_id}").status_code == 404)


# --------------------------------------------------------- check_availability
print("\n[5] check_availability reads only what exists")

wipe()

r = post("/check_availability")
check("empty calendar -> no days", r["days"] == [])
check("empty calendar -> tells agent not to invent a time",
      "not invent" in r["instruction"] or "no availability" in r["instruction"].lower(),
      r["instruction"])

add_slots(D1, ["10:00", "11:00"])
add_slots(D2, ["15:00"])
add_slots(D_FAR, ["11:00"])

r = post("/check_availability")
check("no date -> offers soonest real days", [x["date"] for x in r["days"]][0] == D1.isoformat(),
      str([x["date"] for x in r["days"]]))
check("offers only opened times",
      [s["time_slot"] for s in r["days"][0]["slots"]] == ["10:00", "11:00"])

r = post("/check_availability", date=D_FAR.isoformat())
check("far future day works - nothing is capped",
      r["status"] == "available" and r["days"][0]["date"] == D_FAR.isoformat(),
      r.get("status"))

empty_day = today + timedelta(days=4)
r = post("/check_availability", date=empty_day.isoformat())
check("day with no slots -> no_slots_on_date",
      r["status"] == "no_slots_on_date", r.get("status"))
check("day with no slots still offers alternatives", len(r["days"]) >= 1)

r = post("/check_availability", date="yesterday")
check("past date -> in_the_past",
      r["status"] == "unavailable" and r["reason"] == "in_the_past", r.get("reason"))

r = post("/check_availability", date="whenever suits you")
check("unparseable date handled gracefully", r["status"] == "unclear_date")

r = post("/check_availability", date=D1.isoformat(), language="ar")
check("arabic wording returned",
      any(ord(ch) > 0x600 for ch in r["days"][0]["date_spoken"]))

# A weekend day is bookable if, and only if, somebody opened it. There is no
# hard-coded weekend any more.
friday = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
r = post("/check_availability", date=friday.isoformat())
check("friday closed when no slots opened", r["status"] == "no_slots_on_date")
add_slots(friday, ["11:00"])
r = post("/check_availability", date=friday.isoformat())
check("friday opens once a slot is added - no hard-coded weekend",
      r["status"] == "available", r.get("status"))


# ------------------------------------------------------------------ book_time
print("\n[6] book_time")

wipe()
add_slots(D1, ["10:00", "11:00", "14:00"])
add_slots(D2, ["15:00"])

r = post("/book_time", date=D1.isoformat(), time_slot="11:00 AM",
         prospect_name="Mahmoud", company_name="Scanwave")
check("books an opened slot", r["status"] == "booked" and r["booked"] is True,
      r.get("status"))
check("confirmation is digit-free",
      not any(ch.isdigit() for ch in r["confirmation_spoken"]),
      r.get("confirmation_spoken"))

r = post("/book_time", date=D1.isoformat(), time_slot="11:00")
check("double booking refused", r["status"] == "slot_taken", r.get("status"))
check("double booking offers alternatives", len(r["days"]) >= 1)

r = post("/check_availability", date=D1.isoformat())
check("booked slot disappears from availability",
      "11:00" not in [s["time_slot"] for s in r["days"][0]["slots"]])

r = post("/book_time", date=D1.isoformat(), time_slot="16:00")
check("time that was never opened is refused",
      r["status"] == "slot_not_offered", r.get("status"))
check("refusal tells agent not to invent a time",
      "invent" in r["instruction"], r["instruction"])

r = post("/book_time", date=(today + timedelta(days=5)).isoformat(), time_slot="10:00")
check("day that was never opened is refused",
      r["status"] == "slot_not_offered", r.get("status"))

r = post("/book_time", date="yesterday", time_slot="10:00")
check("past booking refused",
      r["status"] == "unavailable" and r["reason"] == "in_the_past", r.get("status"))

check("missing date refused",
      post("/book_time", time_slot="10:00")["status"] == "missing_date")
check("missing time refused",
      post("/book_time", date=D1.isoformat())["status"] == "missing_time")
check("vague time refused rather than guessed",
      post("/book_time", date=D1.isoformat(),
           time_slot="sometime next week")["status"] == "unclear_time")

r = post("/book_time", date="in 3 days", time_slot="ten in the morning")
check("natural-language date + spoken time both accepted",
      r["status"] == "booked", r.get("status"))

# A slot starting within the lead time must not be bookable.
wipe()
soon = (sch.now() + timedelta(minutes=10))
add_slots(soon.date(), [soon.strftime("%H:%M")])
r = post("/book_time", date=soon.date().isoformat(),
         time_slot=soon.strftime("%H:%M"))
check("slot inside the lead time refused", r["status"] == "too_soon", r.get("status"))
r = post("/check_availability", date=soon.date().isoformat())
check("slot inside the lead time hidden from availability",
      r["status"] == "no_slots_on_date", r.get("status"))


# ------------------------------------------------------- deleting a booking
print("\n[7] cancelling via the dashboard")

wipe()
add_slots(D1, ["10:00"])
post("/book_time", date=D1.isoformat(), time_slot="10:00",
     prospect_name="Lina", company_name="Aqaba Logistics")

slots = client.get("/api/slots").get_json()["slots"]
check("booked slot shows who booked it",
      slots[0]["status"] == "booked" and slots[0]["prospect_name"] == "Lina")

res = client.delete(f"/api/slots/{slots[0]['id']}")
check("a booked slot can be cancelled", res.status_code == 200)
check("cancellation reports what went", res.get_json()["deleted"]["status"] == "booked")
check("calendar is empty afterwards",
      client.get("/api/slots").get_json()["slots"] == [])


# ------------------------------------------------------------------- dashboard
print("\n[8] dashboard wiring")

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
check("health reports open slot count",
      "open_slots" in client.get("/health").get_json())


print(f"\n{'=' * 60}\n{PASS} passed, {FAIL} failed")
if FAILURES:
    print("\nFailures:")
    for f in FAILURES:
        print(f"  - {f}")
sys.exit(1 if FAIL else 0)
