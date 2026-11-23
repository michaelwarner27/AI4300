"""Part / model number / fault-code extraction shared by ingestion and query.

The same functions run over chunk text at ingestion time and over the user's
query at search time, so both sides agree on what counts as a part number and
how it is normalized.

Examples of what is kept (from the manuals):
    457915.70X  640312C85X  8330-733  3106481.009  9330A5991  457915  68356
    CCC2  E1  MACH-1  R-22  RJ-11
Examples of what is dropped:
    3.5  12.4  120  2-86  12VDC  115VAC  24V  5/16  6-3/4  9-Pin  pk/2  1-pc
"""

import re
from typing import List

# Candidate token: alphanumeric runs joined by "." or "-". "/" is a separator,
# which splits fractions (5/16) and "Mfd/370V" into harmless pieces.
_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[.\-][A-Za-z0-9]+)*")

# Number followed by an electrical/physical unit: 12V, 115VAC, 60HZ, 25MFD ...
_UNIT = re.compile(
    r"^\d+(?:\.\d+)?(?:V|VAC|VDC|A|AMP|AMPS|W|HZ|MFD|UF|BTU|BTUH|HP|PSI|PSIG|"
    r"LB|LBS|OZ|IN|FT|MM|CM|RPM|CFM|KW|MA|F|C|K|PH|AWG)$"
)

# Letter segments that mark an ordinary word rather than a code (9-Pin, 1-pc,
# 4-conductor, pk/2 after splitting, Manual1.pdf).
_WORD_SEGMENTS = {"PIN", "PC", "PCS", "EA", "PK", "WAY", "TON", "PDF", "ST", "ND", "RD", "TH"}

_MAX_LETTER_RUN = 4  # CCC, MACH, RJ pass; MANUAL, CONDUCTOR do not
_MIN_PURE_DIGITS = 5  # pure-number tokens need >= 5 digits (457915, 8330-733)


def _is_range(tok: str) -> bool:
    """"561-566" style numeric ranges: two equal-length numbers, increasing.
    Part numbers like 8330-733 have unequal halves and are kept."""
    m = re.fullmatch(r"(\d+)-(\d+)", tok)
    return bool(m) and len(m.group(1)) == len(m.group(2)) and int(m.group(2)) > int(m.group(1))


def _is_part_number(tok: str) -> bool:
    """Decide whether an (uppercased) candidate token looks like a code."""
    if len(tok) < 2 or len(tok) > 24:
        return False
    digits = sum(ch.isdigit() for ch in tok)
    if digits == 0:
        return False

    letter_runs = re.findall(r"[A-Z]+", tok)
    if not letter_runs:
        # Pure number (with separators). Short numbers, decimals, dates and
        # page refs are far too common to be useful identifiers.
        if digits < _MIN_PURE_DIGITS:
            return False
        if "." in tok and len(tok.split(".")[0]) < 4:  # 63.0, 12.45
            return False
        if _is_range(tok):  # 561-566
            return False
        return True

    if _UNIT.match(tok.replace("-", "")):  # 12VDC, 7.5-MFD, 88-108-MFD
        return False
    if any(run in _WORD_SEGMENTS for run in letter_runs):
        return False
    if any(len(run) > _MAX_LETTER_RUN for run in letter_runs):
        return False
    return True


def normalize_part(tok: str) -> str:
    """Canonical form for matching: uppercase, separators removed.

    "457915.70X", "457915-70x" and "457915 70X" style variants all collapse
    to "45791570X".
    """
    return re.sub(r"[\s.\-]", "", tok.upper())


def part_family(tok: str) -> str:
    """Leading digit run of a model number, e.g. "457915.70X" -> "457915".

    Lets a query for the family ("the 457915 manual") match chunks that name a
    specific variant, and vice versa. Returns "" when there is no family
    distinct from the full number.
    """
    m = re.match(r"\d+", tok.upper())
    if not m or len(m.group(0)) < _MIN_PURE_DIGITS:
        return ""
    family = m.group(0)
    return family if family != normalize_part(tok) else ""


def extract_part_numbers(text: str) -> List[str]:
    """Return the distinct part numbers in *text*, as they appear (uppercased),
    in first-seen order."""
    seen = set()
    out: List[str] = []
    for raw in _TOKEN.findall(text or ""):
        tok = raw.upper().strip(".-")
        if tok in seen or not _is_part_number(tok):
            continue
        seen.add(tok)
        out.append(tok)
    return out


def filename_part_keys(filename: str) -> List[str]:
    """Part keys named in a manual's filename ("CCC2-Operating-Instructions.pdf"
    -> ["CCC2"], "Dometic 457915 Installation ... Manual1.pdf" -> ["457915"]).

    Words in filenames are joined by "-"/"_", which would otherwise glue the
    code to ordinary words, so those are split first and the extension dropped.
    """
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename or "")
    return part_keys(" ".join(re.split(r"[-_\s]+", stem)))


# Chroma metadata values must be scalars, so the key list is stored as one
# delimited string. The leading/trailing "|" make every key "|KEY|"-delimited.
_KEY_SEP = "|"


def join_keys(keys: List[str]) -> str:
    return f"{_KEY_SEP}{_KEY_SEP.join(keys)}{_KEY_SEP}" if keys else ""


def split_keys(value: object) -> List[str]:
    if not isinstance(value, str):
        return []
    return [k for k in value.split(_KEY_SEP) if k]


def part_keys(text: str) -> List[str]:
    """Normalized match keys for *text*: each part number plus its family.

    This is what gets stored on a chunk and looked up from a query.
    """
    keys: List[str] = []
    seen = set()
    for tok in extract_part_numbers(text):
        for key in (normalize_part(tok), part_family(tok)):
            if key and key not in seen:
                seen.add(key)
                keys.append(key)
    return keys
