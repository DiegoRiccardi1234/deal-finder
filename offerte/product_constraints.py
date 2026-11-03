"""Deterministic product identities and comparable capacities, without a model catalogue."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


def normalized(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text)).casefold()
    text = re.sub(r"(?<=\w)\+", " plus ", text)
    return " ".join(re.findall(r"[\w]+", text))


@dataclass(frozen=True)
class PhoneIdentity:
    family: str
    variant: tuple[str, ...]


# These are name grammars, not a whitelist of generations or released devices.
_FAMILIES = (
    r"\biphone\s*(\d{1,2})(e)?\b",
    r"\biphone\s*(air|duo|se)\b",
    r"\b(?:samsung\s+)?galaxy\s*([samz]\s*\d{1,3})(fe)?\b",
    r"\b(?:samsung\s+)?galaxy\s*z\s*(fold\s*\d{1,2}|flip\s*\d{1,2})\b",
    r"\b(?:google\s+)?pixel\s*(\d{1,2})(a)?\b",
    r"\b(?:xiaomi\s+)?redmi\s*(note\s*\d{1,2}|\d{1,2})([a-z])?\b",
    r"\bxiaomi\s*(\d{1,2})([a-z])?\b",
    r"\b(?:xiaomi\s+)?poco\s*([fxmc]\s*\d{1,2})\b",
    r"\boneplus\s*(\d{1,2})([a-z])?\b",
    r"\boppo\s*(reno\s*\d{1,2}|find\s*[xn]\s*\d{1,2})\b",
    r"\brealme\s*(?:gt\s*)?(\d{1,2})\b",
    r"\b(?:motorola\s+)?moto\s*([ge]\s*\d{1,3})\b",
    r"\bmotorola\s*(edge\s*\d{1,3}|razr\s*\d{1,3})\b",
    r"\bhonor\s*(?:magic\s*)?(\d{1,3})([a-z])?\b",
    r"\bnothing\s*phone\s*(\d{1,2})([a-z])?\b",
    r"\b(?:huawei\s+)?(?:pura|mate|nova)\s*(\d{1,3})\b",
)
_SUFFIXES = frozenset(
    {
        "pro",
        "max",
        "plus",
        "ultra",
        "mini",
        "lite",
        "fe",
        "xl",
        "fold",
        "flip",
        "edge",
        "neo",
        "power",
        "fusion",
        "play",
    }
)


def phone_identity(title: str) -> PhoneIdentity | None:
    text = normalized(title)
    for pattern in _FAMILIES:
        match = re.search(pattern, text)
        if match is None:
            continue
        family = re.sub(
            r"^(?:samsung|google|xiaomi)\s+(?=galaxy|pixel|redmi|poco)", "", match.group(0)
        )
        attached = match.group(match.lastindex or 1) if (match.lastindex or 0) > 1 else None
        # Letter editions belong to their numerical family, but are distinct models.
        suffix: list[str] = []
        if attached and re.fullmatch(r"[a-z]{1,2}", attached):
            family = family[: -len(attached)].strip()
            suffix.append(attached)
        family = re.sub(r"^(?:huawei)\s+(?=pura|mate|nova)", "", family)
        family = family.replace(" ", "")
        tail = text[match.end() :].strip().split()
        if family == "iphonese" and tail and re.fullmatch(r"20\d{2}|[1-9]", tail[0]):
            suffix.append(tail.pop(0))
        for token in tail:
            if token not in _SUFFIXES:
                break
            suffix.append(token)
        return PhoneIdentity(family, tuple(suffix))
    return None


def matches_product(
    title: str,
    query: str,
    *,
    variant_mode: str = "auto",
    excluded_variants: list[str] | None = None,
) -> bool:
    if variant_mode not in {"auto", "family", "exact"}:
        raise ValueError("Modalità variante non valida")
    requested = phone_identity(query)
    if requested is None:
        return True
    candidate = phone_identity(title)
    if candidate is None or candidate.family != requested.family:
        return False
    variant_text = " ".join(candidate.variant)
    for excluded in excluded_variants or []:
        term = normalized(excluded)
        if term and re.search(rf"(?:^|\s){re.escape(term)}(?:\s|$)", variant_text):
            return False
    exact = variant_mode == "exact" or (variant_mode == "auto" and bool(requested.variant))
    return not exact or candidate.variant == requested.variant


def storage_values(title: str, specs: dict[str, Any] | None = None) -> frozenset[int]:
    """Only capacities evidenced by storage fields or title, excluding labelled RAM."""
    explicit = " ".join(
        str(value)
        for key, value in (specs or {}).items()
        if normalized(str(key)) in {"storage", "storage gb", "archiviazione", "ssd", "hdd"}
        and value is not None
    )
    text = str(explicit or title).casefold()
    text = re.sub(r"\b(?:ram\s*\d{1,3}\s*gb|\d{1,3}\s*gb\s*(?:di\s*)?ram)\b", "", text)
    values = set()
    for match in re.finditer(r"(?<!\d)(\d+(?:\s*[/|]\s*\d+)+)\s*(gb|tb)\b", text):
        for number in re.findall(r"\d+", match.group(1)):
            value = int(number) * (1024 if match.group(2) == "tb" else 1)
            if value >= 32:
                values.add(value)
    for match in re.finditer(r"(?<![\w.])(\d{1,4})\s*(gb|tb)\b", text):
        value = int(match.group(1)) * (1024 if match.group(2) == "tb" else 1)
        # Small unlabelled GB counts in mixed RAM/storage phone titles are ambiguous.
        if value >= 32:
            values.add(value)
    return frozenset(values)


def different_storage(
    title_a: str,
    title_b: str,
    specs_a: dict[str, Any] | None = None,
    specs_b: dict[str, Any] | None = None,
) -> bool:
    first, second = storage_values(title_a, specs_a), storage_values(title_b, specs_b)
    return len(first) == len(second) == 1 and first != second
