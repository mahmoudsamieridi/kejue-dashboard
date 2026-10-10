"""
Date/time helpers for the Kejue outbound agents.

Availability itself is NOT defined here. Slots live in the database and are
created and removed from the dashboard, so this module only knows how to
understand what a prospect said and how to say a date or time out loud.

The voice agents are forbidden (by guardrail) from speaking numerical digits,
so every value handed to an agent comes in two forms:

    value   -> machine form, e.g. "2026-10-14" / "14:30"
    spoken  -> words only,   e.g. "Tuesday the fourteenth of October"
                                 "half past two in the afternoon"
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

BUSINESS_TZ = ZoneInfo("Asia/Amman")

# A slot may not be booked if it starts sooner than this.
MIN_LEAD_MINUTES = 60

# How far ahead to look when searching for alternative days to offer. This is
# a search bound, not a business rule - if no slots exist, none are offered.
SEARCH_HORIZON_DAYS = 365


# --------------------------------------------------------------------------
# numbers as words
# --------------------------------------------------------------------------

_ONES_EN = ["", "one", "two", "three", "four", "five", "six", "seven", "eight",
            "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
            "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS_EN = ["", "", "twenty", "thirty", "forty", "fifty"]

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

# Levantine spoken numbers.
_ONES_AR = ["", "واحد", "اتنين", "تلاتة", "أربعة", "خمسة", "ستة", "سبعة",
            "تمانية", "تسعة", "عشرة", "حدعش", "اتنعش", "تلتعش", "أربعتعش",
            "خمستعش", "ستعش", "سبعتعش", "تمنتعش", "تسعتعش"]
_TENS_AR = ["", "", "عشرين", "تلاتين", "أربعين", "خمسين"]

# Hours take the feminine form after الساعة.
_HOUR_AR = ["", "وحدة", "تنتين", "تلاتة", "أربعة", "خمسة", "ستة", "سبعة",
            "تمانية", "تسعة", "عشرة", "حدعش", "اتنعش"]

_WEEKDAY_AR = ["الاتنين", "الثلاثاء", "الأربعاء", "الخميس",
               "الجمعة", "السبت", "الأحد"]

_MONTH_AR = ["", "كانون ثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران",
             "تموز", "آب", "أيلول", "تشرين أول", "تشرين ثاني", "كانون أول"]


def _words_en(n: int) -> str:
    if n < 20:
        return _ONES_EN[n]
    tens, ones = divmod(n, 10)
    return _TENS_EN[tens] + (f"-{_ONES_EN[ones]}" if ones else "")


def _words_ar(n: int) -> str:
    if n < 20:
        return _ONES_AR[n]
    tens, ones = divmod(n, 10)
    return (f"{_ONES_AR[ones]} و{_TENS_AR[tens]}" if ones else _TENS_AR[tens])


# --------------------------------------------------------------------------
# clock
# --------------------------------------------------------------------------

def today() -> date:
    return datetime.now(BUSINESS_TZ).date()


def now() -> datetime:
    return datetime.now(BUSINESS_TZ)


# --------------------------------------------------------------------------
# saying dates and times out loud
# --------------------------------------------------------------------------

def spoken_date(d: date, lang: str = "en") -> str:
    if lang == "ar":
        return f"{_WEEKDAY_AR[d.weekday()]} {_words_ar(d.day)} {_MONTH_AR[d.month]}"
    return f"{_WEEKDAY_EN[d.weekday()]} the {_ORDINAL_EN[d.day]} of {_MONTH_EN[d.month]}"


def _daypart_en(h: int) -> str:
    if h < 12:
        return "in the morning"
    if h < 18:
        return "in the afternoon"
    return "in the evening"


def _daypart_ar(h: int) -> str:
    if h < 12:
        return "الصبح"
    if h == 12:
        return "الضهر"
    if h < 18:
        return "بعد الضهر"
    return "المسا"


def spoken_time(slot: str, lang: str = "en") -> str:
    """
    Any "HH:MM" rendered as words. Works for whatever times the dashboard
    defines, not a fixed list - so a slot added at, say, nine forty still
    reads correctly and never leaks a digit.
    """
    try:
        h, m = (int(x) for x in str(slot).split(":")[:2])
    except (ValueError, TypeError):
        return str(slot)

    nh = (h + 1) % 24                       # the hour we count towards
    if lang == "ar":
        hw, nhw = _HOUR_AR[h % 12 or 12], _HOUR_AR[nh % 12 or 12]
        part, npart = _daypart_ar(h), _daypart_ar(nh)
        if m == 0:
            return f"{hw} {part}" if not (h == 12) else f"{hw} {part}"
        if m == 15:
            return f"{hw} وربع {part}"
        if m == 20:
            return f"{hw} وتلت {part}"
        if m == 30:
            return f"{hw} ونص {part}"
        if m == 40:
            return f"{nhw} إلا تلت {npart}"
        if m == 45:
            return f"{nhw} إلا ربع {npart}"
        return f"{hw} و{_words_ar(m)} دقيقة {part}"

    hw, nhw = _ONES_EN[h % 12 or 12], _ONES_EN[nh % 12 or 12]
    part, npart = _daypart_en(h), _daypart_en(nh)
    if m == 0:
        return "twelve noon" if h == 12 else f"{hw} {part}"
    if m == 15:
        return f"quarter past {hw} {part}"
    if m == 30:
        return f"half past {hw} {part}"
    if m == 45:
        return f"quarter to {nhw} {npart}"
    if m % 5 == 0:
        if m < 30:
            return f"{_words_en(m)} past {hw} {part}"
        return f"{_words_en(60 - m)} to {nhw} {npart}"
    if m < 10:
        return f"{hw} oh {_words_en(m)} {part}"
    return f"{hw} {_words_en(m)} {part}"


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
    # so the caller can answer "that has already passed" instead of asking the
    # prospect to repeat themselves.
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
    Resolve a date the LLM passed us. Accepts ISO, day-first numeric, relative
    words in English and Arabic, weekday names in both, "in N days/weeks", and
    month-name forms. Raises rather than guessing when nothing matches, so the
    agent gets a usable error instead of a silently wrong booking.
    """
    if raw is None:
        raise DateParseError("no date supplied")

    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw

    ref = reference or today()
    s = str(raw).strip().lower()
    if not s:
        raise DateParseError("empty date")

    for noise in ("on ", "يوم ", "في ", "the "):
        if s.startswith(noise):
            s = s[len(noise):].strip()

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y",
                "%d-%m-%y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass

    for fmt in ("%d-%m", "%d/%m"):
        try:
            p = datetime.strptime(s, fmt).date()
            cand = date(ref.year, p.month, p.day)
            return cand if cand >= ref else date(ref.year + 1, p.month, p.day)
        except ValueError:
            pass

    for word in sorted(_RELATIVE_WORDS, key=len, reverse=True):
        if s == word:
            return ref + timedelta(days=_RELATIVE_WORDS[word])

    # "in 3 days" / "بعد 3 ايام" / "after three days" / "بعد أسبوعين"
    #
    # Unit words must NOT also be counts: "أسبوع" as both 7 and the weekly
    # multiplier made "بعد أسبوع" resolve to seven weeks.
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
            count = 1
        if count:
            return ref + timedelta(days=count * (7 if weekly else 1))

    is_next = any(t in ("next", "الجاي", "القادم", "الجايه") for t in tokens)
    is_last = any(t in ("last", "الماضي", "الماضية", "السابق", "فات") for t in tokens)
    if is_last and any(w in s for w in _WEEK_WORDS):
        return ref - timedelta(days=7)
    for t in tokens:
        if t in _WEEKDAY_WORDS:
            target = _WEEKDAY_WORDS[t]
            if is_last:
                delta = -((ref.weekday() - target) % 7) or -7
                return ref + timedelta(days=delta)
            delta = (target - ref.weekday()) % 7 or 7
            if is_next and delta < 7:
                delta += 7
            return ref + timedelta(days=delta)

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
        return cand if cand >= ref else date(ref.year + 1, month, day)

    raise DateParseError(f"could not understand date {raw!r}")


# --------------------------------------------------------------------------
# time parsing
# --------------------------------------------------------------------------

_WORD_HOUR = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
              "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
              "twelve": 12,
              "وحدة": 1, "واحدة": 1, "تنتين": 2, "تلاتة": 3, "أربعة": 4,
              "خمسة": 5, "ستة": 6, "سبعة": 7, "تمانية": 8, "تسعة": 9,
              "عشرة": 10, "حدعش": 11, "اتنعش": 12}


def parse_slot(raw):
    """
    Normalise a time to "HH:MM", or None if it cannot be understood.

    Accepts "10:00 AM", "10 am", "2 pm", "14:00", "ten in the morning",
    "half past two", "عشرة الصبح", "تلاتة ونص". Whether the result is
    actually bookable is decided by the database, not here.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None

    pm = any(w in s for w in ("pm", "p.m", "afternoon", "evening", "tonight",
                              "بعد الضهر", "بعد الظهر", "المسا", "مسا", "عصرا"))
    am = any(w in s for w in ("am", "a.m", "morning", "الصبح", "صباحا"))
    noon = any(w in s for w in ("noon", "الضهر", "الظهر")) and not pm

    # Fractions, before digit scanning so "half past two" is not read as 2:00.
    half = any(w in s for w in ("half past", "ونص", "و نص"))
    quarter_past = any(w in s for w in ("quarter past", "وربع", "و ربع"))
    quarter_to = any(w in s for w in ("quarter to", "إلا ربع", "الا ربع"))
    third_past = any(w in s for w in ("وتلت", "و تلت"))
    third_to = any(w in s for w in ("إلا تلت", "الا تلت"))

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

    if half:
        minute = 30
    elif quarter_past:
        minute = 15
    elif third_past:
        minute = 20
    elif quarter_to:
        hour, minute = hour - 1, 45
    elif third_to:
        hour, minute = hour - 1, 40

    if hour == 24:
        hour = 0
    if hour < 0:
        hour += 24
    if pm and hour < 12:
        hour += 12
    elif noon and hour == 12:
        hour = 12
    elif not pm and not am and 1 <= hour <= 6:
        hour += 12          # "at two" during business hours means 14:00

    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


# --------------------------------------------------------------------------
# bookability
# --------------------------------------------------------------------------

def slot_datetime(d: date, slot: str) -> datetime:
    h, m = (int(x) for x in slot.split(":")[:2])
    return datetime.combine(d, time(h, m), tzinfo=BUSINESS_TZ)


def slot_is_bookable(d: date, slot: str) -> bool:
    """A slot must be far enough ahead to actually be attended."""
    return slot_datetime(d, slot) >= now() + timedelta(minutes=MIN_LEAD_MINUTES)
