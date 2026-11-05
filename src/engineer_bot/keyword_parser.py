"""Offline Indonesian parser.

Deterministic, no I/O, no network. Used when the AI is unavailable, when the
operator picks keyword mode, and by the dry-run simulator. It is deliberately
narrow: it understands the vocabulary of this game and nothing else.
"""

from __future__ import annotations

import re

from . import config
from .models import Command, Verb

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

UNITS: dict[str, int] = {
    "satu": 1,
    "dua": 2,
    "tiga": 3,
    "empat": 4,
    "lima": 5,
    "enam": 6,
    "tujuh": 7,
    "delapan": 8,
    "sembilan": 9,
}
MULTIPLIERS = {"puluh", "belas", "ratus", "seratus", "sepuluh", "sebelas"}
NUMBER_WORDS = set(UNITS) | MULTIPLIERS

DIRECTION_WORDS = {"kiri": "KIRI", "kanan": "KANAN"}

#: (phrase, verb) — MUST stay sorted by phrase length, longest first.
_VERB_PHRASES: list[tuple[str, Verb]] = [
    ("taruh tablet", Verb.TAMBAH),
    ("masukkan tablet", Verb.TAMBAH),
    ("masukan tablet", Verb.TAMBAH),
    ("tambah tablet", Verb.TAMBAH),
    ("tambahkan tablet", Verb.TAMBAH),
    ("putar sendok", Verb.ADUK),
    ("aduk dengan sendok", Verb.ADUK),
    ("jalan ke depan", Verb.MAJU),
    ("berjalan ke depan", Verb.MAJU),
    ("melangkah ke depan", Verb.MAJU),
    ("langkah ke depan", Verb.MAJU),
    ("tunggu dulu", Verb.TUNGGU),
    ("ke belakang", Verb.MUNDUR),
    ("tumpahkan", Verb.TUANG),
    ("tambahkan", Verb.TAMBAH),
    ("masukkan", Verb.TAMBAH),
    ("masukan", Verb.TAMBAH),
    ("letakkan", Verb.TARUH),
    ("letakan", Verb.TARUH),
    ("taruhkan", Verb.TARUH),
    ("berputar", Verb.BELOK),
    ("mengaduk", Verb.ADUK),
    ("menuang", Verb.TUANG),
    ("melangkah", Verb.MAJU),
    ("maju", Verb.MAJU),
    ("kedepan", Verb.MAJU),
    ("depan", Verb.MAJU),
    ("mundur", Verb.MUNDUR),
    ("kembali", Verb.MUNDUR),
    ("belok", Verb.BELOK),
    ("putar", Verb.BELOK),
    ("ambil", Verb.AMBIL),
    ("angkat", Verb.AMBIL),
    ("pegang", Verb.AMBIL),
    ("pungut", Verb.AMBIL),
    ("tuang", Verb.TUANG),
    ("siram", Verb.TUANG),
    ("tunggu", Verb.TUNGGU),
    ("diam", Verb.TUNGGU),
    ("tambah", Verb.TAMBAH),
    ("aduk", Verb.ADUK),
    ("taruh", Verb.TARUH),
    ("simpan", Verb.TARUH),
    ("letak", Verb.TARUH),
]
_VERB_PHRASES.sort(key=lambda item: len(item[0]), reverse=True)

#: Longest phrase first so "belok ke kiri" wins over "kekiri".
_FIXES = sorted(config.SPEECH_FIXES.items(), key=lambda item: len(item[0]), reverse=True)
_FILLERS = set(config.SPEECH_FILLERS)

#: Cheap guard against players trying to talk to the model instead of the robot.
_INJECTION_MARKERS = (
    "abaikan",
    "ignore",
    "system prompt",
    "prompt",
    "aturan baru",
    "terima semua",
    "kamu adalah",
    "you are",
    "jailbreak",
)


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, repair known mishearings, drop fillers.

    Speech engines return "eh, maju dua langka" with no punctuation and the odd
    wrong word. The repairs here are the ones that cannot change meaning: they
    never add a number, a direction or an object that was not spoken.
    """
    lowered = text.lower()
    cleaned = re.sub(r"[^0-9a-z\u00c0-\u024f]+", " ", lowered)
    cleaned = " ".join(cleaned.split())
    for wrong, right in _FIXES:
        cleaned = re.sub(rf"\b{re.escape(wrong)}\b", right, cleaned)
    words = [word for word in cleaned.split() if word not in _FILLERS]
    return " ".join(words)


def _eval_number(sequence: list[str]) -> int:
    total = 0
    current = 0
    for token in sequence:
        if token in UNITS:
            if current >= 10:
                total += current
                current = UNITS[token]
            else:
                current = UNITS[token]
        elif token == "sepuluh":
            if current >= 10:
                total += current
            current = 10
        elif token == "sebelas":
            current = (current + 10) if current else 11
        elif token == "belas":
            current += 10
        elif token == "puluh":
            current = (current or 1) * 10
        elif token == "ratus":
            current = (current or 1) * 100
        elif token == "seratus":
            total += 100
    return total + current


def parse_number(tokens: list[str]) -> int | None:
    """First number in the token list, as digits or Indonesian words."""
    for token in tokens:
        if token.isdigit():
            return int(token)
    for index, token in enumerate(tokens):
        if token not in NUMBER_WORDS:
            continue
        sequence: list[str] = []
        cursor = index
        while cursor < len(tokens) and tokens[cursor] in NUMBER_WORDS:
            sequence.append(tokens[cursor])
            cursor += 1
        value = _eval_number(sequence)
        if value:
            return value
    return None


def _find_verbs(text: str) -> tuple[set[Verb], str]:
    """Return the verbs present and the text with those spans blanked out."""
    occupied = [False] * len(text)
    found: set[Verb] = set()
    chars = list(text)
    for phrase, verb in _VERB_PHRASES:
        for match in re.finditer(rf"\b{re.escape(phrase)}\b", text):
            start, end = match.span()
            if any(occupied[start:end]):
                continue
            for index in range(start, end):
                occupied[index] = True
            found.add(verb)
            for index in range(start, end):
                chars[index] = " "
    return found, "".join(chars)


def _find_objects(text: str) -> list[str]:
    """Canonical object names in order of appearance, longest alias first."""
    hits: list[tuple[int, int, str]] = []
    for canonical, aliases in config.OBJECT_ALIASES.items():
        for alias in aliases:
            for match in re.finditer(rf"\b{re.escape(alias)}\b", text):
                hits.append((match.start(), match.end(), canonical))
    hits.sort(key=lambda hit: (hit[0], -(hit[1] - hit[0])))

    ordered: list[str] = []
    last_end = -1
    for start, end, canonical in hits:
        if start < last_end:
            continue
        ordered.append(canonical)
        last_end = end
    return ordered


def _invalid(reason: str) -> Command:
    return Command(verb=Verb.INVALID, alasan=reason)


def parse_keyword(text: str) -> Command:
    """Best-effort translation. Returns an INVALID command when unsure."""
    normalized = normalize(text)
    if not normalized:
        # Nothing left after stripping fillers: empty means truly empty, but
        # "tolong dong" was speech we simply could not use.
        return _invalid(config.MSG_EMPTY if not text.strip() else config.MSG_UNKNOWN_VERB)

    if any(marker in normalized for marker in _INJECTION_MARKERS):
        return _invalid(config.MSG_UNKNOWN_VERB)

    verbs, _remainder = _find_verbs(normalized)
    if not verbs:
        return _invalid(config.MSG_UNKNOWN_VERB)
    if len(verbs) > 1:
        return _invalid(config.MSG_TOO_GENERAL)

    verb = next(iter(verbs))
    # Objects are read from the whole sentence: verb phrases such as
    # "aduk dengan sendok" carry their object inside the phrase itself.
    objects = _find_objects(normalized)
    tokens = normalized.split()
    number = parse_number(tokens)

    if verb in (Verb.MAJU, Verb.MUNDUR):
        return Command(verb=verb, n=number)

    if verb is Verb.BELOK:
        direction = next((value for word, value in DIRECTION_WORDS.items() if word in tokens), None)
        return Command(verb=verb, arah=direction, n=number)

    if verb is Verb.TUNGGU:
        return Command(verb=verb, n=number)

    if verb is Verb.AMBIL:
        return Command(verb=verb, objek=objects[0] if objects else None)

    if verb is Verb.TUANG:
        target = "corong" if "corong" in objects else None
        source = next((obj for obj in objects if obj != "corong"), None)
        return Command(verb=verb, objek=source, target=target)

    if verb is Verb.TAMBAH:
        objek = "tablet" if "tablet" in objects else (objects[0] if objects else None)
        target = "toples" if "toples" in objects else None
        return Command(verb=verb, objek=objek, target=target)

    if verb is Verb.ADUK:
        return Command(verb=verb, objek="sendok" if "sendok" in objects else None)

    if verb is Verb.TARUH:
        objek = objects[0] if objects else None
        target = objects[1] if len(objects) > 1 else None
        # "taruh tablet ke toples" is a TAMBAH in disguise.
        if objek == "tablet":
            return Command(verb=Verb.TAMBAH, objek="tablet", target="toples")
        return Command(verb=verb, objek=objek, target=target)

    return _invalid(config.MSG_UNKNOWN_VERB)  # pragma: no cover
