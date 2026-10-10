"""
Date-aware scheduling helpers for the Kejue outbound agents.

The voice agents are forbidden (by guardrail) from speaking numerical digits, so
every value this module hands back to an agent comes in two forms:

    value   -> machine form, e.g. "2026-10-14" / "10:00"
    spoken  -> words only,   e.g. "Tuesday the fourteenth of October"

Dates are resolved in the business timezone (Asia/Amman) and never in UTC, so
"tomorrow" means tomorrow for the sales team, not tomorrow in GMT.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

BUSINESS_TZ = ZoneInfo("Asia/Amman")

# Slots are stored as 24h "HH:MM" so they sort correctly and never collide.
SLOT_TIMES = ["10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00"]

# Monday=0 ... Sunday=6. Jordan's weekend is Friday + Saturday.
WEEKEND_DAYS = {4, 5}

# How far ahead a prospect may book, and how soon a meeting may start.
MAX_HORIZON_DAYS = 60
MIN_LEAD_MINUTES = 60


# --------------------------------------------------------------------------
# number / date / time wording  (digits are never spoken)
# --------------------------------------------------------------------------

_ORDINAL_EN = [
    "", "first", "second", "third", "fourth", "fifth", "sixth", "seventh",
    "eighth", "ninth", "tenth", "eleventh", "twelfth", "thirteenth",
    "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth",
    "nineteenth", "twentieth", "twenty-first", "twenty-second",
    "twenty-third", "twenty-fourth", "twenty-fifth", "twenty-sixth",
    "twenty-seventh", "twenty-eighth", "twenty-ninth", "thirtieth",
    "thirty-first",
]

_WEEKDAY_EN = ["Monday", "Tuesday", "Wednesday", "Thursday",
               "Friday", "Saturday", "Sunday"]

_MONTH_EN = ["", "January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]

# Levantine spoken numbers - how a Jordanian actually says a day of the month.
_DAYNUM_AR = [
    "", "واحد", "اتنين", "تلاتة", "أربعة", "خمسة", "ستة", "سبعة", "تمانية",
    "تسعة", "عشرة", "حدعش", "اتنعش", "تلتعش", "أربعتعش", "خمستعش", "ستعش",
    "سبعتعش", "تمنتعش", "تسعتعش", "عشرين", "واحد وعشرين", "اتنين وعشرين",
    "تلاتة وعشرين", "أربعة وعشرين", "خمسة وعشرين", "ستة وعشرين",
    "سبعة وعشرين", "تمانية وعشرين", "تسعة وعشرين", "تلاتين", "واحد وتلاتين",
]

_WEEKDAY_AR = ["الاتنين", "الثلاثاء", "الأربعاء", "الخميس",
               "الجمعة", "السبت", "الأحد"]

# Levantine month names (used in Jordan far more than the Gregorian loanwords).
_MONTH_AR = ["", "كانون ثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران",
             "تموز", "آب", "أيلول", "تشرين أول", "تشرين ثاني", "كانون أول"]

_TIME_SPOKEN = {
    "10:00": ("ten in the morning", "عشرة الصبح"),
    "11:00": ("eleven in the morning", "حدعش الصبح"),
    "12:00": ("twelve noon", "اتنعش الضهر"),
    "13:00": ("one in the afternoon", "واحدة بعد الضهر"),
    "14:00": ("two in the afternoon", "تنتين بعد الضهر"),
    "15:00": ("three in the afternoon", "تلاتة بعد الضهر"),
    "16:00": ("four in the afternoon", "أربعة بعد الضهر"),
    "17:00": ("five in the afternoon", "خمسة بعد الضهر"),
}


def today() -> date:
    """Today in the business timezone."""
    return datetime.now(BUSINESS_TZ).date()


def now() -> datetime:
    return datetime.now(BUSINESS_TZ)


def spoken_date(d: date, lang: str = "en") -> str:
    if lang == "ar":
        return f"{_WEEKDAY_AR[d.weekday()]} {_DAYNUM_AR[d.day]} {_MONTH_AR[d.month]}"
    return f"{_WEEKDAY_EN[d.weekday()]} the {_ORDINAL_EN[d.day]} of {_MONTH_EN[d.month]}"


def spoken_time(slot: str, lang: str = "en") -> str:
    en, ar = _TIME_SPOKEN.get(slot, (slot, slot))
    return ar if lang == "ar" else en


def spoken_slot(d: date, slot: str, lang: str = "en") -> str:
    if lang == "ar":
        return f"{spoken_date(d, 'ar')} الساعة {spoken_time(slot, 'ar')}"
    return f"{spoken_date(d, 'en')} at {spoken_time(slot, 'en')}"


# --------------------------------------------------------------------------
# flexible date parsing
# --------------------------------------------------------------------------

_WEEKDAY_WORDS = {
    "monday": 0, "mon": 0, "الاتنين": 0, "الإثنين": 0, "اتنين": 0, "الاثنين": 0,
    "tuesday": 1, "tue": 1, "tues": 1, "الثلاثاء": 1, "الثلاثا": 1, "تلاتا": 1,
    "wednesday": 2, "wed": 2, "الأربعاء": 2, "الاربعاء": 2, "أربعا": 2,
    "thursday": 3, "thu": 3, "thurs": 3, "الخميس": 3, "خميس": 3,
    "friday": 4, "fri": 4, "الجمعة": 4, "جمعة": 4,
    "saturday": 5, "sat": 5, "السبت": 5, "سبت": 5,
    "sunday": 6, "sun": 6, "الأحد": 6, "الاحد": 6, "حد": 6,
}

_RELATIVE_WORDS = {
    "today": 0, "اليوم": 0, "النهاردة": 0,
    "tomorrow": 1, "tmrw": 1, "بكرا": 1, "بكره": 1, "غدا": 1, "غدًا": 1,
    "day after tomorrow": 2, "overmorrow": 2, "بعد بكرا": 2, "بعد بكره": 2,
    # Past references resolve to real past dates rather than failing to parse,
    # so classify_date() can return "in_the_past" and the agent apologises and
    # moves on instead of asking the prospect to repeat themselves.
    "yesterday": -1, "امبارح": -1, "مبارح": -1, "البارحة": -1, "مبارحة": -1,
    "day before yesterday": -2, "أول امبارح": -2, "اول امبارح": -2,
}

_MONTH_WORDS = {}
for _i, _m in enumerate(_MONTH_EN):
    if _m:
        _MONTH_WORDS[_m.lower()] = _i
        _MONTH_WORDS[_m.lower()[:3]] = _i
for _i, _m in enumerate(_MONTH_AR):
    if _m:
        _MONTH_WORDS[_m] = _i


class DateParseError(ValueError):
    """Raised when a caller-supplied date string cannot be understood."""


def parse_date(raw, reference: date = None) -> date:
    """
    Resolve a date the LLM passed us. Accepts, in rough order of preference:

        "2026-10-14"              ISO (what the tool description asks for)
        "14/10/2026", "14-10"     day-first numeric
        "today" / "tomorrow"      relative words, English or Arabic
        "in 3 days"
        "next tuesday", "tuesday" weekday names, English or Arabic
        "October 14", "14 October"

    Raises DateParseError rather than guessing when nothing matches, so the
    agent gets a usable error instead of a silently wrong booking.
    """
    if raw is None:
        raise DateParseError("no date supplied")

    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()

    ref = reference or today()
    s = str(raw).strip().lower()
    if not s:
        raise DateParseError("empty date")

    # Strip filler the model sometimes includes.
    for noise in ("on ", "يوم ", "في ", "the "):
        if s.startswith(noise):
            s = s[len(noise):].strip()

    # ISO / numeric, longest-first so "2026-10-14" wins over "14-10".
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y",
                "%d-%m-%y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass

    # Day/month with no year -> pick the next occurrence.
    for fmt in ("%d-%m", "%d/%m"):
        try:
            p = datetime.strptime(s, fmt).date()
            cand = date(ref.year, p.month, p.day)
            if cand < ref:
                cand = date(ref.year + 1, p.month, p.day)
            return cand
        except ValueError:
            pass

    # Relative words (check two-word forms first).
    for word in sorted(_RELATIVE_WORDS, key=len, reverse=True):
        if s == word:
            return ref + timedelta(days=_RELATIVE_WORDS[word])

    # "in 3 days" / "بعد 3 ايام" / "after three days" / "بعد أسبوعين"
    #
    # The unit words must NOT also be counts. "أسبوع" previously sat in _SMALL
    # as 7 *and* set the weekly multiplier, so "بعد أسبوع" came out as seven
    # weeks instead of seven days.
    tokens = s.replace("،", " ").split()
    _SMALL = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
              "seven": 7, "eight": 8, "nine": 9, "ten": 10, "a": 1, "an": 1,
              "واحد": 1, "يومين": 2, "تلاتة": 3, "تلات": 3, "أربعة": 4,
              "خمسة": 5, "ستة": 6, "سبعة": 7, "عشرة": 10}
    _WEEK_WORDS = ("week", "اسبوع", "أسبوع", "اسابيع", "أسابيع")
    _TWO_WEEK_WORDS = ("أسبوعين", "اسبوعين", "fortnight")

    if any(t in ("in", "after", "بعد", "خلال") for t in tokens):
        weekly = any(w in s for w in _WEEK_WORDS)
        if any(w in s for w in _TWO_WEEK_WORDS):
            return ref + timedelta(days=14)

        count = None
        for t in tokens:
            v = _SMALL.get(t)
            if v is None and t.isdigit():
                v = int(t)
            if v:
                count = v
                break
        if count is None and weekly:
            count = 1                     # "in a week" / "بعد أسبوع"
        if count:
            return ref + timedelta(days=count * (7 if weekly else 1))

    # Weekday names. "next X" means the X in the following week; "last X" looks
    # backwards, which classify_date() then rejects as a past date.
    is_next = any(t in ("next", "الجاي", "القادم", "الجايه") for t in tokens)
    is_last = any(t in ("last", "الماضي", "الماضية", "السابق", "فات") for t in tokens)
    if is_last and any(w in s for w in ("week", "اسبوع", "أسبوع")):
        return ref - timedelta(days=7)
    for t in tokens:
        if t in _WEEKDAY_WORDS:
            target = _WEEKDAY_WORDS[t]
            if is_last:
                delta = -((ref.weekday() - target) % 7)
                if delta == 0:
                    delta = -7       # "last Tuesday" on a Tuesday = a week ago
                return ref + timedelta(days=delta)
            delta = (target - ref.weekday()) % 7
            if delta == 0:
                delta = 7            # "tuesday" on a Tuesday = next Tuesday
            if is_next and delta < 7:
                delta += 7
            return ref + timedelta(days=delta)

    # "October 14" / "14 October" / "14 تشرين أول"
    month = None
    day = None
    for t in tokens:
        if t in _MONTH_WORDS:
            month = _MONTH_WORDS[t]
        elif t.strip(",").isdigit():
            day = int(t.strip(","))
    if month is None:
        for word, idx in _MONTH_WORDS.items():
            if len(word) > 3 and word in s:
                month = idx
                break
    if month and day and 1 <= day <= 31:
        cand = date(ref.year, month, day)
        if cand < ref:
            cand = date(ref.year + 1, month, day)
        return cand

    raise DateParseError(f"could not understand date {raw!r}")


def parse_slot(raw):
    """
    Normalise a time the LLM passed us to one of SLOT_TIMES, or None.

    Accepts "10:00 AM", "10 am", "10:00", "ten in the morning", "2 pm",
    "14:00", "الساعة عشرة", etc.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None

    if s in SLOT_TIMES:
        return s

    # Word forms first, so "ten in the morning" resolves before digit parsing.
    for slot, (en, ar) in _TIME_SPOKEN.items():
        if s == en.lower() or s == ar:
            return slot
    _WORD_HOUR = {"ten": 10, "eleven": 11, "twelve": 12, "one": 1, "two": 2,
                  "three": 3, "four": 4, "five": 5,
                  "عشرة": 10, "حدعش": 11, "اتنعش": 12, "واحدة": 1, "وحدة": 1,
                  "تنتين": 2, "تلاتة": 3, "أربعة": 4, "خمسة": 5}

    pm = any(w in s for w in ("pm", "p.m", "afternoon", "evening",
                              "بعد الضهر", "بعد الظهر", "المسا", "عصرا"))
    am = any(w in s for w in ("am", "a.m", "morning", "الصبح", "صباحا"))
    noon = any(w in s for w in ("noon", "الضهر", "الظهر")) and not pm

    hour = None
    minute = 0
    digits = "".join(c if c.isdigit() else " " for c in s).split()
    if digits:
        hour = int(digits[0])
        if len(digits) > 1 and len(digits[1]) == 2:
            minute = int(digits[1])
    else:
        for word, h in _WORD_HOUR.items():
            if word in s:
                hour = h
                break

    if hour is None:
        return None
    if minute not in (0,):
        return None                      # we only offer top-of-hour slots

    if hour == 24:
        hour = 0
    if pm and hour < 12:
        hour += 12
    elif noon and hour == 12:
        hour = 12
    elif not pm and not am and hour < 10:
        hour += 12                       # "at 2" during business hours = 14:00

    candidate = f"{hour:02d}:00"
    return candidate if candidate in SLOT_TIMES else None


# --------------------------------------------------------------------------
# calendar rules
# --------------------------------------------------------------------------

def is_working_day(d: date) -> bool:
    return d.weekday() not in WEEKEND_DAYS


def slot_datetime(d: date, slot: str) -> datetime:
    h, m = (int(x) for x in slot.split(":"))
    return datetime.combine(d, time(h, m), tzinfo=BUSINESS_TZ)


def slot_is_bookable(d: date, slot: str) -> bool:
    """A slot must be far enough in the future to actually be attended."""
    return slot_datetime(d, slot) >= now() + timedelta(minutes=MIN_LEAD_MINUTES)


def horizon_end() -> date:
    return today() + timedelta(days=MAX_HORIZON_DAYS)


def classify_date(d: date):
    """
    Return (ok, reason) describing whether `d` can host a meeting at all.
    reason is a machine token the route turns into agent-readable text.
    """
    if d < today():
        return False, "in_the_past"
    if d > horizon_end():
        return False, "beyond_horizon"
    if not is_working_day(d):
        return False, "weekend"
    return True, None


def free_slots(d: date, booked: set) -> list:
    """Open, still-attendable slots on `d`. `booked` is a set of "HH:MM"."""
    return [s for s in SLOT_TIMES
            if s not in booked and slot_is_bookable(d, s)]


def next_working_days(start: date, count: int) -> list:
    out = []
    cursor = start
    limit = horizon_end()
    while len(out) < count and cursor <= limit:
        if is_working_day(cursor):
            out.append(cursor)
        cursor += timedelta(days=1)
    return out
