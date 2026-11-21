#!/usr/bin/env python3
"""SextantArc - offline celestial navigation worksheets.

A single-file, standard-library-only command line tool for offshore work:
reduce a celestial sight to an intercept (F1), correct a meridian altitude
and derive latitude from it (F2), and keep a per-voyage chronometer
error/rate log in a local JSON file (F3).

Everything the tool needs comes from flags or the local store file: no
network, no ephemeris, no clock arithmetic beyond stamping "recorded at".
GHA and declination are always read from the paper almanac by the navigator.

The committed design direction (Monochrome Statement) is committed in this
file too, in DESIGN_TOKENS / DESIGN_FONTS / DESIGN_ROOT_CSS, and the render
functions execute it in characters: hierarchy from scale and weight, one
rationed accent, an inverse banner as the signature.

Exit codes
    0  success
    1  a --self-test check failed
    2  usage or validation error
    3  storage error (store file unreadable or unwritable)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

APP_NAME = "SextantArc"
APP_VERSION = "0.1.0"
PROG = "sextantarc"

STORE_SCHEMA_VERSION = 1
DEFAULT_STORE_PATH = Path("~/.sextantarc/store.json")

EXIT_OK = 0
EXIT_CHECK_FAILED = 1
EXIT_USAGE = 2
EXIT_STORAGE = 3

RATE_WARNING_SECONDS_PER_DAY = 5.0
SUN_SEMI_DIAMETER_ARCMIN = 16.0
SUN_PARALLAX_ARCMIN = 0.15
MOON_SD_TO_HP_RATIO = 0.2725

# ---------------------------------------------------------------------------
# Committed design direction: "Monochrome Statement"
# ---------------------------------------------------------------------------
# SextantArc prints plain text - the plan rules out any GUI, TUI or curses
# surface - so the design source of truth lives in the one deliverable file
# and is executed in characters by the render functions:
#   * near-monochrome ink on paper, hierarchy from scale and weight;
#   * one accent, rationed to a single job (the Result: line);
#   * the signature inverse chip: an uppercase letterspaced banner on solid
#     ink, sharp corners, carried by every worksheet title and its rule;
#   * deliberately static: no animation, no colour codes, no decoration.
# DESIGN_ROOT_CSS is the same token set in CSS form for any companion surface
# that wants to match the sheets; the tool itself serves no HTML.

DESIGN_TOKENS = {
    "--background": "#f7fbfe",
    "--foreground": "#0e1318",
    "--card": "#feffff",
    "--card-foreground": "#0e1318",
    "--primary": "#182127",
    "--on-primary": "#ffffff",
    "--secondary": "#e9edf0",
    "--on-secondary": "#0e1318",
    "--muted": "#e9edf0",
    "--muted-foreground": "#5d646a",
    "--accent": "#007eb7",
    "--on-accent": "#ffffff",
    "--destructive": "#c9302d",
    "--on-destructive": "#ffffff",
    "--border": "#dbdfe2",
    "--ring": "#007eb7",
}

DESIGN_FONTS = {
    "heading": "'Space Mono', ui-monospace, 'Courier New', monospace",
    "body": "'Work Sans', system-ui, -apple-system, 'Segoe UI', sans-serif",
}

DESIGN_MEASURE = 78  # column measure every printed line respects

DESIGN_ROOT_CSS = (
    "@import url('https://fonts.googleapis.com/css2?"
    "family=Space+Mono:wght@400;700&family=Work+Sans:wght@400;500;700&display=swap');\n"
    "\n"
    ":root {\n"
    + "".join(f"  {token}: {value};\n" for token, value in DESIGN_TOKENS.items())
    + "  --font-heading: "
    + DESIGN_FONTS["heading"]
    + ";\n"
    "  --font-body: "
    + DESIGN_FONTS["body"]
    + ";\n"
    "  --text-display: clamp(2rem, 6vw, 7.5rem);\n"
    "  --text-title: 1.75rem;\n"
    "  --text-section: 1.0625rem;\n"
    "  --text-body: 0.9375rem;\n"
    "  --text-meta: 0.8125rem;\n"
    "  --text-micro: 0.6875rem;\n"
    "  --space-1: 4px;\n"
    "  --space-2: 8px;\n"
    "  --space-3: 12px;\n"
    "  --space-4: 16px;\n"
    "  --space-6: 24px;\n"
    "  --space-8: 32px;\n"
    "  --space-12: 48px;\n"
    "  --measure: 78ch;\n"
    "  --radius: 2px;\n"
    "  --duration: 120ms;\n"
    "}"
)

REDUCE_USAGE = (
    "usage: sextantarc reduce --gha GHA --dec DEC --lat LAT --lon LON --ho HO\n"
    "                       [--body NAME] [--export PATH [--force]]\n"
    "example: sextantarc reduce --gha 30 --dec \"10°00.0'N\" "
    "--lat \"40°00.0'N\" --lon 0 --ho \"49°40.0'\""
)
MERIDIAN_USAGE = (
    "usage: sextantarc meridian --lat LAT --dec DEC --hs HS\n"
    "                          [--body sun|moon|planet|star]\n"
    "                          [--limb lower|upper|none] [--ie ARCMIN]\n"
    "                          [--height-of-eye METRES] [--hp ARCMIN]\n"
    "example: sextantarc meridian --body sun --limb lower \\\n"
    "             --lat \"40°00.0'N\" --dec \"10°00.0'N\" \\\n"
    "             --hs \"59°45.0'\" --height-of-eye 3"
)
CHRONO_ADD_USAGE = (
    "usage: sextantarc chrono add --voyage NAME --utc 2024-06-01T12:00:00Z\n"
    "                            --reading 12:00:12.5 --error 12.5\n"
    "                            --direction fast|slow\n"
    "                            [--rate SECONDS_PER_DAY] [--note TEXT]\n"
    "example: sextantarc chrono add --voyage \"Cape Horn 2024\" \\\n"
    "             --utc 2024-06-01T12:00:00Z --reading 12:00:12.5 \\\n"
    "             --error +12.5 --direction fast --note \"first comparison\""
)
VOYAGE_NEW_USAGE = (
    "usage: sextantarc voyage new --name NAME [--notes TEXT]\n"
    "example: sextantarc voyage new --name \"Cape Horn 2024\" "
    "--notes \"Falmouth to Ushuaia\""
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UsageError(Exception):
    """Bad input: a flag is missing, unparsable or out of range (exit 2)."""


class StorageError(Exception):
    """The store file cannot be read or written safely (exit 3)."""


def ruled_title(title: str) -> list[str]:
    """A title line plus an underline sized to the title."""
    return [title, "=" * len(title)]


# ---------------------------------------------------------------------------
# Angles: parsing, formatting, validation
# ---------------------------------------------------------------------------

_ANGLE_RE = re.compile(
    r"""^\s*
        (?P<sign>[+-])?
        (?P<deg>\d+(?:\.\d+)?)
        \s*
        (?:
            [°d:]\s*(?P<min1>\d+(?:\.\d+)?)?\s*[′']?\s*
          | (?P<min2>\d+(?:\.\d+)?)\s*[′']?\s*
        )?
        (?P<hemi>[NSEWnsew])?
        \s*$""",
    re.VERBOSE,
)

_ANGLE_EXAMPLES = "45°12.6'N, \"45 12.6 N\" or -45.21"

_KIND_RULES = {
    "lat": ("latitude", "NS", 90.0),
    "dec": ("declination", "NS", 90.0),
    "lon": ("longitude", "EW", 180.0),
}

_ALTITUDE_LABELS = {"ho": "observed altitude", "hs": "sextant altitude"}


def parse_angle(raw: str, kind: str, flag: str) -> float:
    """Parse a degrees-and-decimal-minutes string into signed degrees.

    kind is one of lat, lon, dec, gha or a key of _ALTITUDE_LABELS.
    Positive is north/east. Range and hemisphere letters are validated here
    so a transposed digit is caught before it reaches a worksheet.
    """
    text = str(raw).strip()
    match = _ANGLE_RE.match(text)
    if not match:
        raise UsageError(
            f"{flag}: cannot read {raw!r} as degrees and decimal minutes "
            f"(accepted shapes: {_ANGLE_EXAMPLES})"
        )

    sign = -1.0 if match.group("sign") == "-" else 1.0
    degrees = float(match.group("deg"))
    minute_text = match.group("min1") or match.group("min2")
    minutes = float(minute_text) if minute_text else 0.0
    if minutes >= 60.0:
        raise UsageError(
            f"{flag}: minutes must be below 60 (got {minutes:g}′ in {raw!r})"
        )
    hemisphere = (match.group("hemi") or "").upper()
    magnitude = degrees + minutes / 60.0
    value = sign * magnitude

    if kind == "gha":
        if hemisphere:
            raise UsageError(
                f"{flag}: GHA is measured 0-360° west of Greenwich and takes "
                f"no hemisphere letter (got {hemisphere!r} in {raw!r})"
            )
        if not 0.0 <= value <= 360.0:
            raise UsageError(
                f"{flag}: GHA must be between 0 and 360 degrees (got {raw!r})"
            )
        return value % 360.0

    if kind in _KIND_RULES:
        label, allowed, limit = _KIND_RULES[kind]
        if hemisphere and hemisphere not in allowed:
            raise UsageError(
                f"{flag}: {label} takes a {allowed[0]} or {allowed[1]} "
                f"hemisphere letter, not {hemisphere!r} (got {raw!r})"
            )
        if hemisphere and sign < 0:
            raise UsageError(
                f"{flag}: the hemisphere letter {hemisphere!r} conflicts with "
                f"the minus sign in {raw!r}"
            )
        if hemisphere == allowed[1]:
            value = -magnitude
        if abs(value) > limit:
            raise UsageError(
                f"{flag}: {label} must be between 0 and {limit:g} degrees "
                f"({allowed[0]} or {allowed[1]}); got {raw!r}"
            )
        return value

    if kind in _ALTITUDE_LABELS:
        label = _ALTITUDE_LABELS[kind]
        if hemisphere:
            raise UsageError(
                f"{flag}: {label} is a plain angle and takes no hemisphere "
                f"letter (got {hemisphere!r} in {raw!r})"
            )
        if not 0.0 <= value <= 90.0:
            raise UsageError(
                f"{flag}: {label} must be between 0 and 90 degrees "
                f"(got {raw!r})"
            )
        return value

    raise ValueError(f"unknown angle kind {kind!r}")


def split_degrees_minutes(value: float) -> tuple[int, float]:
    """Split signed decimal degrees into whole degrees and decimal minutes."""
    total_minutes = round(abs(value) * 60.0, 1)
    degrees = int(total_minutes // 60)
    minutes = round(total_minutes - degrees * 60, 1)
    if minutes >= 60.0:
        degrees += 1
        minutes = 0.0
    return degrees, minutes


def format_dm(value: float) -> str:
    """Format a plain angle as 30°00.0′."""
    degrees, minutes = split_degrees_minutes(value)
    return f"{degrees}°{minutes:04.1f}′"


def format_position(value: float, kind: str) -> str:
    """Format latitude/longitude/declination as 45° 12.6′ N."""
    degrees, minutes = split_degrees_minutes(value)
    if kind == "lon":
        hemisphere = "E" if value >= 0 else "W"
    else:
        hemisphere = "N" if value >= 0 else "S"
    return f"{degrees}° {minutes:04.1f}′ {hemisphere}"


def parse_float(raw: str, flag: str, label: str) -> float:
    try:
        value = float(str(raw).strip())
    except ValueError as exc:
        raise UsageError(f"{flag}: {label} must be a number (got {raw!r})") from exc
    if not math.isfinite(value):
        raise UsageError(f"{flag}: {label} must be a finite number (got {raw!r})")
    return value


def parse_arcminutes(raw: str, flag: str, label: str, limit: float) -> float:
    text = str(raw).strip()
    if text.endswith("′") or text.endswith("'"):
        text = text[:-1]
    value = parse_float(text, flag, label)
    if abs(value) > limit:
        raise UsageError(
            f"{flag}: {label} must be between -{limit:g} and {limit:g} "
            f"arcminutes (got {raw!r})"
        )
    return value

# ---------------------------------------------------------------------------
# Sight reduction (F1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SightReduction:
    body: str
    gha_deg: float
    dec_deg: float
    assumed_lat_deg: float
    assumed_lon_deg: float
    ho_deg: float
    lha_deg: float
    hc_deg: float
    z_deg: float
    zn_deg: float
    intercept_nm: float
    toward_away: str


def normalize_degrees(angle: float) -> float:
    """Normalise an angle into [0, 360)."""
    return angle % 360.0


def computed_altitude(lat_deg: float, dec_deg: float, lha_deg: float) -> float:
    """Hc from sin Hc = sin Lat sin Dec + cos Lat cos Dec cos LHA."""
    lat = math.radians(lat_deg)
    dec = math.radians(dec_deg)
    lha = math.radians(lha_deg)
    sine_hc = (
        math.sin(lat) * math.sin(dec)
        + math.cos(lat) * math.cos(dec) * math.cos(lha)
    )
    return math.degrees(math.asin(max(-1.0, min(1.0, sine_hc))))


def azimuth_angle(
    lat_deg: float, dec_deg: float, lha_deg: float, hc_deg: float
) -> tuple[float, float]:
    """Return (Z, Zn) in degrees.

    Z is the azimuth angle in [0, 180] from
    acos[(sin Dec - sin Lat sin Hc) / (cos Lat cos Hc)];
    Zn = 360 - Z when LHA < 180, otherwise Zn = Z.
    """
    lat = math.radians(lat_deg)
    dec = math.radians(dec_deg)
    lha = math.radians(lha_deg)
    hc = math.radians(hc_deg)
    denominator = math.cos(lat) * math.cos(hc)

    if abs(denominator) > 1e-9:
        ratio = (math.sin(dec) - math.sin(lat) * math.sin(hc)) / denominator
        z = math.degrees(math.acos(max(-1.0, min(1.0, ratio))))
    else:
        # Body near the zenith or observer at a pole: the acos form is
        # ill-conditioned, so take the same angle from the atan2 form.
        direct = math.degrees(
            math.atan2(
                -math.cos(dec) * math.sin(lha),
                math.sin(dec) * math.cos(lat)
                - math.cos(dec) * math.sin(lat) * math.cos(lha),
            )
        ) % 360.0
        z = direct if lha_deg >= 180.0 else (360.0 - direct) % 360.0

    zn = (360.0 - z) if lha_deg < 180.0 else z
    return z, zn % 360.0


def reduce_sight(
    body: str,
    gha_deg: float,
    dec_deg: float,
    lat_deg: float,
    lon_deg: float,
    ho_deg: float,
) -> SightReduction:
    """Run the intercept method for one already-corrected sight."""
    lha = normalize_degrees(gha_deg + lon_deg)
    hc = computed_altitude(lat_deg, dec_deg, lha)
    z, zn = azimuth_angle(lat_deg, dec_deg, lha, hc)
    intercept = (ho_deg - hc) * 60.0
    return SightReduction(
        body=body,
        gha_deg=gha_deg,
        dec_deg=dec_deg,
        assumed_lat_deg=lat_deg,
        assumed_lon_deg=lon_deg,
        ho_deg=ho_deg,
        lha_deg=lha,
        hc_deg=hc,
        z_deg=z,
        zn_deg=zn,
        intercept_nm=intercept,
        toward_away="Toward" if intercept >= 0 else "Away",
    )


def render_sight_worksheet(sight: SightReduction) -> str:
    """Render the step-numbered worksheet as fixed-width plain text."""
    lat = math.radians(sight.assumed_lat_deg)
    dec = math.radians(sight.dec_deg)
    sine_hc = (
        math.sin(lat) * math.sin(dec)
        + math.cos(lat) * math.cos(dec) * math.cos(math.radians(sight.lha_deg))
    )
    away = sight.intercept_nm < 0
    magnitude = abs(sight.intercept_nm)
    sign_word = "less" if sight.lha_deg < 180.0 else "more"
    lines = [
        *ruled_title(f"{APP_NAME} {APP_VERSION} - sight reduction worksheet"),
        "",
        "Inputs (read from the almanac)",
        f"  Body                       {sight.body}",
        f"  GHA                        {format_dm(sight.gha_deg)}",
        f"  Declination (Dec)          {format_position(sight.dec_deg, 'dec')}",
        f"  Assumed latitude           "
        f"{format_position(sight.assumed_lat_deg, 'lat')}",
        f"  Assumed longitude          "
        f"{format_position(sight.assumed_lon_deg, 'lon')}",
        f"  Observed altitude (Ho)     {format_dm(sight.ho_deg)}",
        "",
        "Steps",
        "  1. LHA = GHA + assumed longitude (east positive), normalised 0-360",
        f"     LHA = {format_dm(sight.gha_deg)} "
        f"{'+' if sight.assumed_lon_deg >= 0 else '-'} "
        f"{format_dm(abs(sight.assumed_lon_deg))} "
        f"= {format_dm(sight.lha_deg)}",
        "  2. sin Hc = sin Lat sin Dec + cos Lat cos Dec cos LHA",
        f"     sin Hc = {sine_hc:.6f}  ->  Hc = {format_dm(sight.hc_deg)}",
        "  3. Z = acos[(sin Dec - sin Lat sin Hc) / (cos Lat cos Hc)]",
        f"     Z = {sight.z_deg:.1f}°; LHA {format_dm(sight.lha_deg)} is "
        f"{sign_word} than 180°",
        f"     Zn = {'360° - Z' if sight.lha_deg < 180.0 else 'Z'} "
        f"= {sight.zn_deg:.1f}°",
        "  4. Intercept = (Ho - Hc) x 60 nautical miles",
        f"     ({format_dm(sight.ho_deg)} - {format_dm(sight.hc_deg)}) x 60 "
        f"= {sight.intercept_nm:+.1f} nm",
        f"     Ho is {'lower' if away else 'higher'} than Hc, so the "
        f"position lies {magnitude:.1f} nm",
        f"     {'away from' if away else 'toward'} the body's geographical "
        f"position (GP).",
        "",
        f"Result: Ho {format_dm(sight.ho_deg)}, Hc {format_dm(sight.hc_deg)}, "
        f"Zn {sight.zn_deg:.1f}°",
        f"        intercept {magnitude:.1f} nm {sight.toward_away}, "
        f"LHA {format_dm(sight.lha_deg)}",
    ]
    return "\n".join(lines) + "\n"

# ---------------------------------------------------------------------------
# Meridian altitude correction (F2)
# ---------------------------------------------------------------------------

MERIDIAN_BODIES = ("sun", "moon", "planet", "star")
MERIDIAN_LIMBS = ("lower", "upper", "none")


@dataclass(frozen=True)
class MeridianCorrection:
    body: str
    limb: str
    dr_lat_deg: float
    dec_deg: float
    hs_deg: float
    index_correction_arcmin: float
    height_of_eye_m: float
    hp_arcmin: float | None
    dip_arcmin: float
    refraction_arcmin: float
    semi_diameter_arcmin: float
    parallax_arcmin: float
    ho_corrected_deg: float
    lat_result_deg: float
    rule_line: str


def dip_arcmin(height_of_eye_m: float) -> float:
    """Dip of the sea horizon in arcminutes (negative: subtract it)."""
    return -1.76 * math.sqrt(max(0.0, height_of_eye_m))


def refraction_arcmin(apparent_altitude_deg: float) -> float:
    """Bennett's closed-form refraction in arcminutes (negative: subtract)."""
    altitude = apparent_altitude_deg
    if altitude >= 89.9:
        return 0.0
    if altitude <= -1.0:
        return 0.0
    argument = math.radians(altitude + 7.31 / (altitude + 4.4))
    return -1.0 / math.tan(argument)


def semi_diameter_arcmin(body: str, limb: str, hp_arcmin: float | None) -> float:
    """Signed semi-diameter: positive for a lower limb, negative for upper."""
    if limb == "none":
        return 0.0
    if body == "sun":
        radius = SUN_SEMI_DIAMETER_ARCMIN
    elif body == "moon":
        # sin SD = 0.2725 sin HP; the plan does not claim 0.1′ for the Moon.
        parallax = hp_arcmin if hp_arcmin is not None else 57.0
        radius = math.degrees(
            math.asin(MOON_SD_TO_HP_RATIO * math.sin(math.radians(parallax / 60.0)))
        ) * 60.0
    else:
        return 0.0
    return radius if limb == "lower" else -radius


def parallax_arcmin(body: str, hp_arcmin: float | None, apparent_altitude_deg: float) -> float:
    """Parallax in altitude: HP cos(Ho), 0.15′ for the Sun, 0 for stars."""
    cosine = math.cos(math.radians(apparent_altitude_deg))
    if body == "moon":
        horizontal = hp_arcmin if hp_arcmin is not None else 57.0
        return horizontal * cosine
    if body == "sun":
        return SUN_PARALLAX_ARCMIN * cosine
    if hp_arcmin is not None:
        return hp_arcmin * cosine
    return 0.0


def correct_meridian_altitude(
    body: str,
    limb: str,
    dr_lat_deg: float,
    dec_deg: float,
    hs_deg: float,
    index_correction_arcmin: float,
    height_of_eye_m: float,
    hp_arcmin: float | None,
) -> MeridianCorrection:
    """Walk IC, dip, refraction, semi-diameter and parallax to a latitude."""
    after_index = hs_deg + index_correction_arcmin / 60.0
    dip = dip_arcmin(height_of_eye_m)
    apparent = after_index + dip / 60.0
    refraction = refraction_arcmin(apparent)
    diameter = semi_diameter_arcmin(body, limb, hp_arcmin)
    parallax = parallax_arcmin(body, hp_arcmin, apparent)
    corrected = apparent + (refraction + diameter + parallax) / 60.0

    zenith_distance = 90.0 - corrected
    bearing_north = dec_deg > dr_lat_deg
    same_name = (dec_deg >= 0.0) == (dr_lat_deg >= 0.0)
    latitude = dec_deg - zenith_distance if bearing_north else dec_deg + zenith_distance

    name_line = "same name" if same_name else "contrary name"
    bearing_line = "north" if bearing_north else "south"
    operation = "-" if bearing_north else "+"
    rule = (
        f"Dec {format_position(dec_deg, 'dec')} and DR latitude "
        f"{format_position(dr_lat_deg, 'lat')} are {name_line};\n"
        f"        the body bears {bearing_line} at meridian passage, so\n"
        f"        Lat = Dec {operation} (90° - Ho)."
    )
    return MeridianCorrection(
        body=body,
        limb=limb,
        dr_lat_deg=dr_lat_deg,
        dec_deg=dec_deg,
        hs_deg=hs_deg,
        index_correction_arcmin=index_correction_arcmin,
        height_of_eye_m=height_of_eye_m,
        hp_arcmin=hp_arcmin,
        dip_arcmin=dip,
        refraction_arcmin=refraction,
        semi_diameter_arcmin=diameter,
        parallax_arcmin=parallax,
        ho_corrected_deg=corrected,
        lat_result_deg=latitude,
        rule_line=rule,
    )


def render_meridian_table(result: MeridianCorrection) -> str:
    """Render the correction table and the latitude derivation."""
    body_label = result.body.capitalize()
    limb_label = f"{result.limb} limb" if result.limb != "none" else "no limb"
    rows: list[tuple[str, str | None, float]] = []
    running = result.hs_deg
    rows.append(("Sextant altitude hs", None, running))
    running = result.hs_deg + result.index_correction_arcmin / 60.0
    rows.append(
        (
            "Index correction (IE)",
            f"{result.index_correction_arcmin:+.1f}′",
            running,
        )
    )
    running += result.dip_arcmin / 60.0
    rows.append(
        (
            f"Dip (height of eye {result.height_of_eye_m:g} m)",
            f"{result.dip_arcmin:+.1f}′",
            running,
        )
    )
    running += result.refraction_arcmin / 60.0
    rows.append(
        (
            f"Refraction (Bennett, {result.hs_deg:.2f}° apparent)",
            f"{result.refraction_arcmin:+.1f}′",
            running,
        )
    )
    running += result.semi_diameter_arcmin / 60.0
    rows.append(
        (
            f"Semi-diameter ({limb_label}, {body_label})",
            f"{result.semi_diameter_arcmin:+.1f}′",
            running,
        )
    )
    running += result.parallax_arcmin / 60.0
    rows.append(
        (
            f"Parallax in altitude ({body_label})",
            f"{result.parallax_arcmin:+.1f}′",
            running,
        )
    )

    lines = [
        *ruled_title(f"{APP_NAME} {APP_VERSION} - meridian altitude correction table"),
        "",
        "Observation",
        f"  Body / limb                {body_label} / {limb_label}",
        f"  DR latitude                "
        f"{format_position(result.dr_lat_deg, 'lat')}",
        f"  Declination (Dec)          {format_position(result.dec_deg, 'dec')}",
        f"  Sextant altitude (hs)      {format_dm(result.hs_deg)}",
        f"  Index correction (IE)      {result.index_correction_arcmin:+.1f}′",
        f"  Height of eye              {result.height_of_eye_m:g} m",
        "",
        "Corrections (arcminutes, running total to the right)",
    ]
    for label, value, total in rows:
        value_text = "" if value is None else value
        lines.append(f"  {label:<38}{value_text:>7}  {format_dm(total):>9}")
    lines.extend(
        [
            "  " + "-" * 56,
            f"  {'Corrected altitude Ho':<38}{'':>7}  "
            f"{format_dm(result.ho_corrected_deg):>9}",
            "",
            "Latitude",
            f"  Rule: {result.rule_line}",
            f"  90° - Ho = {format_dm(90.0 - result.ho_corrected_deg)}",
            "",
        ]
    )
    difference = (result.lat_result_deg - result.dr_lat_deg) * 60.0
    lines.append(
        f"Result: latitude {format_position(result.lat_result_deg, 'lat')} "
        f"(DR {format_position(result.dr_lat_deg, 'lat')}, "
        f"{abs(difference):.1f}′ apart)"
    )
    if abs(difference) > 30.0:
        lines.append(
            "Check: far from the DR position - re-read hs, Dec and limb."
        )
    return "\n".join(lines) + "\n"

# ---------------------------------------------------------------------------
# Store: voyages and chronometer entries (F3)
# ---------------------------------------------------------------------------

_UTC_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?Z$"
)
_READING_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2}(?:\.\d+)?)$")


def default_store_path() -> Path:
    """The store file every command uses unless --file overrides it."""
    return Path(os.path.expanduser(str(DEFAULT_STORE_PATH)))


def resolve_store_path(args: argparse.Namespace) -> Path:
    override = getattr(args, "file", None)
    if override:
        return Path(os.path.expanduser(str(override)))
    return default_store_path()


def timestamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(raw: str) -> datetime:
    """Parse a UTC stamp written as 2024-06-01T12:00:00Z."""
    text = str(raw).strip()
    match = _UTC_RE.match(text)
    if not match:
        raise UsageError(
            f"--utc: UTC time must look like 2024-06-01T12:00:00Z (got {raw!r})"
        )
    year, month, day, hour, minute, second, fraction = match.groups()
    microsecond = int(float(f"0.{fraction}") * 1_000_000) if fraction else 0
    try:
        return datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second),
            microsecond,
            tzinfo=timezone.utc,
        )
    except ValueError as exc:
        raise UsageError(
            f"--utc: {raw!r} is not a real calendar time in UTC"
        ) from exc


def parse_reading(raw: str) -> str:
    """Validate a chronometer reading written as HH:MM:SS.s."""
    text = str(raw).strip()
    match = _READING_RE.match(text)
    if not match:
        raise UsageError(
            "--reading: chronometer reading must look like HH:MM:SS.s "
            f"(e.g. 12:00:12.5), got {raw!r}"
        )
    hours, minutes, seconds = match.groups()
    if int(hours) > 23 or int(minutes) > 59 or float(seconds) >= 61.0:
        raise UsageError(
            f"--reading: {raw!r} is out of range (hours 0-23, minutes 0-59, "
            "seconds below 61)"
        )
    return text


def slugify(name: str) -> str:
    """Turn a voyage name into its stable id."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "voyage"


def empty_store() -> dict:
    return {
        "schema_version": STORE_SCHEMA_VERSION,
        "app": APP_NAME,
        "app_version": APP_VERSION,
        "voyages": [],
    }


def load_store(path: Path) -> tuple[dict, bool]:
    """Read the store. Returns (store, existed).

    Raises StorageError when the file is unreadable or not a valid store;
    the caller must then leave the file exactly as it found it.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_store(), False
    except OSError as exc:
        reason = exc.strerror or str(exc)
        raise StorageError(
            f"Could not read {path}: {reason} - nothing was overwritten"
        ) from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StorageError(
            f"Could not read {path}: invalid JSON — fix or move this file; "
            "nothing was overwritten"
        ) from exc

    if not isinstance(data, dict) or not isinstance(data.get("voyages"), list):
        raise StorageError(
            f"Could not read {path}: unexpected structure (expected an object "
            "with a voyages list) - fix or move this file; nothing was "
            "overwritten"
        )

    voyages: list[dict] = []
    for voyage in data["voyages"]:
        if not isinstance(voyage, dict) or "id" not in voyage or "name" not in voyage:
            raise StorageError(
                f"Could not read {path}: a voyage record is missing its id or "
                "name - fix or move this file; nothing was overwritten"
            )
        entries = voyage.get("entries")
        voyage["entries"] = list(entries) if isinstance(entries, list) else []
        voyages.append(voyage)
    data["voyages"] = voyages
    data.setdefault("schema_version", STORE_SCHEMA_VERSION)
    data.setdefault("app", APP_NAME)
    data.setdefault("app_version", APP_VERSION)
    return data, True


def save_store(path: Path, store: dict) -> None:
    """Write the store atomically: temp file in the same directory, then
    os.replace, mode 0600. A failed write never touches the old file."""
    payload = json.dumps(store, indent=2, ensure_ascii=False) + "\n"
    temporary_name = ""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle_fd, temporary_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=".store-", suffix=".json"
        )
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    except OSError as exc:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                temporary_name = ""
        reason = exc.strerror or str(exc)
        raise StorageError(
            f"Could not write {path}: {reason} - the existing file was left "
            "untouched"
        ) from exc


def find_voyage(store: dict, query: str) -> dict | None:
    slug = slugify(query)
    wanted = query.strip().casefold()
    for voyage in store["voyages"]:
        if voyage["id"] == slug or str(voyage["name"]).strip().casefold() == wanted:
            return voyage
    return None


def new_voyage(name: str, notes: str = "") -> dict:
    return {
        "id": slugify(name),
        "name": name.strip(),
        "created_utc": timestamp_now(),
        "notes": notes or "",
        "entries": [],
    }


def signed_error(entry: dict) -> float:
    """Chronometer error with fast positive and slow negative, in seconds."""
    magnitude = abs(float(entry.get("error_seconds", 0.0) or 0.0))
    return magnitude if entry.get("error_direction") == "fast" else -magnitude


def sorted_entries(voyage: dict) -> list[dict]:
    def key(entry: dict) -> tuple[datetime, int]:
        try:
            observed = parse_utc(str(entry.get("observed_utc", "")))
        except UsageError:
            observed = datetime.min.replace(tzinfo=timezone.utc)
        return observed, int(entry.get("id", 0) or 0)

    return sorted(voyage.get("entries", []), key=key)


def interval_gaps(voyage: dict) -> list[tuple[dict, dict, float, float]]:
    """Yield (previous, current, drift_seconds, days) oldest first."""
    gaps: list[tuple[dict, dict, float, float]] = []
    entries = sorted_entries(voyage)
    for previous, current in zip(entries, entries[1:]):
        days = (
            parse_utc(str(current["observed_utc"]))
            - parse_utc(str(previous["observed_utc"]))
        ).total_seconds() / 86400.0
        drift = signed_error(current) - signed_error(previous)
        gaps.append((previous, current, drift, days))
    return gaps


def format_error(entry: dict) -> str:
    magnitude = abs(float(entry.get("error_seconds", 0.0) or 0.0))
    direction = entry.get("error_direction", "fast")
    return f"{magnitude:.1f} s {direction}"


def format_stamp(stamp: str) -> str:
    return str(stamp).replace("T", " ").replace("Z", "Z")


def describe_voyage(voyage: dict) -> str:
    return f"{voyage['name']} ({voyage['id']})"


def wrap_hanging(
    text: str, indent: str, width: int = DESIGN_MEASURE
) -> list[str]:
    """Wrap text into lines of at most `width` columns under `indent`."""
    words = text.split()
    if not words:
        return [indent.rstrip()]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(indent) + len(candidate) > width:
            lines.append(indent + current)
            current = word
        else:
            current = candidate
    lines.append(indent + current)
    return lines


def render_chrono_list(voyages: list[dict], store_path: Path) -> str:
    total_entries = sum(len(voyage.get("entries", [])) for voyage in voyages)
    lines = [
        *ruled_title(f"{APP_NAME} {APP_VERSION} - chronometer log"),
        f"Store: {store_path}",
        "",
    ]
    if total_entries == 0:
        lines.extend(
            [
                "No chronometer entries yet.",
                "",
                "Record the first comparison with:",
                "  sextantarc chrono add --voyage \"Cape Horn 2024\" \\",
                "      --utc 2024-06-01T12:00:00Z --reading 12:00:12.5 \\",
                "      --error 12.5 --direction fast --note \"first comparison\"",
            ]
        )
        return "\n".join(lines) + "\n"

    for voyage in voyages:
        entries = sorted_entries(voyage)
        if not entries:
            continue
        count = len(entries)
        lines.extend(
            wrap_hanging(
                f"{describe_voyage(voyage)} - {count} "
                f"{'entry' if count == 1 else 'entries'}",
                "",
            )
        )
        gaps = {
            id(current): (drift, days)
            for _previous, current, drift, days in interval_gaps(voyage)
        }
        for entry in reversed(entries):
            lines.append(
                f"  {format_stamp(str(entry['observed_utc']))}  "
                f"reading {entry.get('chronometer_reading', '--')}  "
                f"error {format_error(entry)}"
            )
            gap = gaps.get(id(entry))
            if gap is None:
                lines.append("      first entry for this voyage")
            else:
                drift, days = gap
                if days > 0:
                    rate = drift / days
                    lines.append(
                        f"      since previous: {drift:+.1f} s, "
                        f"{rate:+.1f} s/day over {days:.1f} days"
                    )
                else:
                    lines.append(
                        f"      since previous: {drift:+.1f} s, "
                        "same UTC time as the previous entry"
                    )
            note = str(entry.get("note", "") or "").strip()
            if note:
                lines.extend(wrap_hanging(f"note: {note}", "      "))
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def voyage_summary(voyage: dict) -> dict:
    entries = sorted_entries(voyage)
    gaps = interval_gaps(voyage)
    net_drift = (
        signed_error(entries[-1]) - signed_error(entries[0]) if len(entries) > 1 else 0.0
    )
    days = (
        (
            parse_utc(str(entries[-1]["observed_utc"]))
            - parse_utc(str(entries[0]["observed_utc"]))
        ).total_seconds()
        / 86400.0
        if len(entries) > 1
        else 0.0
    )
    rate_samples = [drift / day for _p, _c, drift, day in gaps if day > 0]
    mean_rate = sum(rate_samples) / len(rate_samples) if rate_samples else None
    return {
        "entries": entries,
        "count": len(entries),
        "net_drift": net_drift,
        "days": days,
        "mean_rate": mean_rate,
    }


def render_chrono_show(voyage: dict) -> str:
    summary = voyage_summary(voyage)
    lines = [f"Voyage    {describe_voyage(voyage)}"]
    if voyage.get("notes"):
        lines.append(f"Notes     {voyage['notes']}")
    if summary["count"] == 0:
        lines.extend(
            [
                "Entries   0",
                "",
                "No entries for this voyage yet. Add one with:",
                f"  sextantarc chrono add --voyage \"{voyage['name']}\" "
                "--utc 2024-06-01T12:00:00Z \\",
                "      --reading 12:00:12.5 --error 12.5 --direction fast",
            ]
        )
        return "\n".join(lines) + "\n"

    entries = summary["entries"]
    first, last = entries[0], entries[-1]
    lines.append(f"Entries   {summary['count']}")
    lines.append(
        f"First     {format_error(first)} at "
        f"{format_stamp(str(first['observed_utc']))}"
    )
    lines.append(
        f"Last      {format_error(last)} at "
        f"{format_stamp(str(last['observed_utc']))}"
    )
    if summary["count"] > 1:
        lines.append(f"Elapsed   {summary['days']:.1f} days")
        trend = "gaining" if summary["net_drift"] >= 0 else "losing"
        lines.append(f"Net drift {summary['net_drift']:+.1f} s ({trend})")
    if summary["mean_rate"] is not None:
        mean_rate = summary["mean_rate"]
        lines.append(f"Mean rate {mean_rate:+.1f} s/day")
        if abs(mean_rate) > RATE_WARNING_SECONDS_PER_DAY:
            lines.append("")
            lines.append(
                f"WARNING: mean rate {mean_rate:+.1f} s/day is above "
                f"{RATE_WARNING_SECONDS_PER_DAY:g} s/day - recheck the "
                "comparisons before using this rate."
            )
    else:
        lines.append("Mean rate n/a (one entry, or both entries share a UTC time)")
    return "\n".join(lines) + "\n"


def render_voyage_list(voyages: list[dict], store_path: Path) -> str:
    lines = [
        *ruled_title(f"{APP_NAME} {APP_VERSION} - voyages"),
        f"Store: {store_path}",
        "",
    ]
    if not voyages:
        lines.extend(
            [
                "No voyages yet.",
                "",
                "Create one with:",
                "  sextantarc voyage new --name \"Cape Horn 2024\" \\",
                "      --notes \"Falmouth to Ushuaia\"",
            ]
        )
        return "\n".join(lines) + "\n"

    for voyage in voyages:
        count = len(voyage.get("entries", []))
        created = str(voyage.get("created_utc", ""))[:10] or "--"
        label = "entry" if count == 1 else "entries"
        lines.append(
            f"  {str(voyage['id'])[:24]:<24} {created}  "
            f"{str(voyage['name'])[:28]:<28} {count} {label}"
        )
    return "\n".join(lines) + "\n"

# ---------------------------------------------------------------------------
# Self-test (F4): offline numeric and storage checks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


SIGHT_EXPECTATION = {
    "hc_deg": 49.9017,
    "zn_deg": 229.86,
    "intercept_nm": -14.1,
    "hc_tolerance_deg": 0.01,
    "zn_tolerance_deg": 0.1,
    "intercept_tolerance_nm": 0.1,
}

MERIDIAN_EXPECTATION = {
    "ho_deg": 59.9574,
    "lat_deg": 40.0426,
    "tolerance_deg": 0.02,
}


def check_sight_reduction() -> CheckResult:
    expected = SIGHT_EXPECTATION
    sight = reduce_sight(
        "Sun", 30.0, 10.0, 40.0, 0.0, 49.0 + 40.0 / 60.0
    )
    hc_ok = abs(sight.hc_deg - expected["hc_deg"]) <= expected["hc_tolerance_deg"]
    zn_ok = abs(sight.zn_deg - expected["zn_deg"]) <= expected["zn_tolerance_deg"]
    intercept_ok = (
        abs(sight.intercept_nm - expected["intercept_nm"])
        <= expected["intercept_tolerance_nm"]
    )
    detail = (
        f"Hc {format_dm(sight.hc_deg)} (expected "
        f"{format_dm(expected['hc_deg'])}), Zn {sight.zn_deg:.1f}° (expected "
        f"{expected['zn_deg']:.1f}°), intercept {abs(sight.intercept_nm):.1f} nm "
        f"{sight.toward_away} (expected "
        f"{abs(expected['intercept_nm']):.1f} nm Away)"
    )
    return CheckResult("sight reduction", hc_ok and zn_ok and intercept_ok, detail)


def check_meridian_correction() -> CheckResult:
    expected = MERIDIAN_EXPECTATION
    result = correct_meridian_altitude(
        "sun", "lower", 40.0, 10.0, 59.0 + 45.0 / 60.0, 0.0, 3.0, None
    )
    ho_ok = abs(result.ho_corrected_deg - expected["ho_deg"]) <= expected["tolerance_deg"]
    lat_ok = abs(result.lat_result_deg - expected["lat_deg"]) <= expected["tolerance_deg"]
    detail = (
        f"Ho {format_dm(result.ho_corrected_deg)} (expected "
        f"{format_dm(expected['ho_deg'])}), latitude "
        f"{format_position(result.lat_result_deg, 'lat')} (expected "
        f"{format_position(expected['lat_deg'], 'lat')})"
    )
    return CheckResult("meridian correction", ho_ok and lat_ok, detail)


def check_store_round_trip() -> CheckResult:
    with tempfile.TemporaryDirectory(prefix="sextantarc-selftest-") as directory:
        path = Path(directory) / "store.json"
        store, existed = load_store(path)
        voyage = new_voyage("Self-test voyage", "round-trip check")
        store["voyages"].append(voyage)
        voyage["entries"].append(
            {
                "id": 1,
                "observed_utc": "2024-06-01T12:00:00Z",
                "chronometer_reading": "12:00:12.5",
                "error_seconds": 12.5,
                "error_direction": "fast",
                "rate_seconds_per_day": None,
                "note": "round trip",
                "recorded_utc": "2024-06-01T12:05:00Z",
            }
        )
        save_store(path, store)
        reloaded, existed_after = load_store(path)
        mode_ok = (path.stat().st_mode & 0o777) == 0o600
    round_trip_ok = (
        not existed
        and existed_after
        and len(reloaded["voyages"]) == 1
        and reloaded["voyages"][0]["id"] == voyage["id"]
        and len(reloaded["voyages"][0]["entries"]) == 1
        and reloaded["voyages"][0]["entries"][0]["error_seconds"] == 12.5
    )
    detail = (
        f"wrote and read back 1 voyage with 1 entry, file mode "
        f"{'0600' if mode_ok else 'not 0600'}"
    )
    return CheckResult("store round-trip", round_trip_ok and mode_ok, detail)


def run_self_test() -> int:
    checks = [check_sight_reduction(), check_meridian_correction(), check_store_round_trip()]
    lines = [*ruled_title(f"{APP_NAME} {APP_VERSION} - self-test"), ""]
    for check in checks:
        status = "PASS" if check.ok else "FAIL"
        lines.append(f"  {status}  {check.name}: {check.detail}")
    passed = sum(1 for check in checks if check.ok)
    lines.append("")
    lines.append(f"{passed}/{len(checks)} checks passed")
    sys.stdout.write("\n".join(lines) + "\n")
    return EXIT_OK if passed == len(checks) else EXIT_CHECK_FAILED


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def missing_options(pairs: Sequence[tuple[str, object]], usage: str) -> None:
    missing = [flag for flag, value in pairs if value is None]
    if missing:
        raise UsageError(
            "missing required option(s): "
            + ", ".join(missing)
            + "\n"
            + usage
            + f"\nhint: run '{PROG} --help' for the full command list"
        )


def prepare_export(export_path: str | None, force: bool) -> Path | None:
    if not export_path:
        return None
    path = Path(os.path.expanduser(str(export_path)))
    if path.exists() and not force:
        raise UsageError(f"{export_path} exists; pass --force to overwrite")
    if path.is_dir():
        raise UsageError(f"{export_path} is a directory; give a file path")
    return path


def write_export(path: Path | None, text: str) -> None:
    if path is None:
        return
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        reason = exc.strerror or str(exc)
        raise StorageError(f"Could not write {path}: {reason}") from exc
    print(f"Worksheet written to {path.resolve()}", file=sys.stderr)


def cmd_reduce(args: argparse.Namespace) -> int:
    missing_options(
        (
            ("--gha", args.gha),
            ("--dec", args.dec),
            ("--lat", args.lat),
            ("--lon", args.lon),
            ("--ho", args.ho),
        ),
        REDUCE_USAGE,
    )
    gha = parse_angle(args.gha, "gha", "--gha")
    dec = parse_angle(args.dec, "dec", "--dec")
    lat = parse_angle(args.lat, "lat", "--lat")
    lon = parse_angle(args.lon, "lon", "--lon")
    ho = parse_angle(args.ho, "ho", "--ho")
    export_path = prepare_export(args.export, args.force)
    sight = reduce_sight(args.body or "Sun", gha, dec, lat, lon, ho)
    text = render_sight_worksheet(sight)
    sys.stdout.write(text)
    write_export(export_path, text)
    return EXIT_OK


def cmd_meridian(args: argparse.Namespace) -> int:
    missing_options(
        (("--lat", args.lat), ("--dec", args.dec), ("--hs", args.hs)),
        MERIDIAN_USAGE,
    )
    body = (args.body or "sun").strip().lower()
    if body not in MERIDIAN_BODIES:
        raise UsageError(
            f"--body: {args.body!r} is not one of "
            + ", ".join(MERIDIAN_BODIES)
        )
    default_limb = "lower" if body in ("sun", "moon") else "none"
    limb = (args.limb or default_limb).strip().lower()
    if limb not in MERIDIAN_LIMBS:
        raise UsageError(
            f"--limb: {args.limb!r} is not one of " + ", ".join(MERIDIAN_LIMBS)
        )
    if body in ("star", "planet") and limb != "none":
        raise UsageError(
            "--limb: stars and planets show no semi-diameter; pass "
            "--limb none"
        )
    hp: float | None = None
    if args.hp is not None:
        hp = parse_arcminutes(args.hp, "--hp", "horizontal parallax", 70.0)
    if body == "moon" and hp is None:
        raise UsageError(
            "--hp: the Moon's horizontal parallax is required (almanac HP in "
            "arcminutes, e.g. 59.0)"
        )
    index_correction = (
        parse_arcminutes(args.ie, "--ie", "index correction", 30.0)
        if args.ie is not None
        else 0.0
    )
    height_of_eye = (
        parse_float(args.height_of_eye, "--height-of-eye", "height of eye")
        if args.height_of_eye is not None
        else 0.0
    )
    if not 0.0 <= height_of_eye <= 100.0:
        raise UsageError(
            "--height-of-eye: height of eye must be between 0 and 100 metres "
            f"(got {args.height_of_eye!r})"
        )
    lat = parse_angle(args.lat, "lat", "--lat")
    dec = parse_angle(args.dec, "dec", "--dec")
    hs = parse_angle(args.hs, "hs", "--hs")
    export_path = prepare_export(args.export, args.force)

    result = correct_meridian_altitude(
        body, limb, lat, dec, hs, index_correction, height_of_eye, hp
    )
    text = render_meridian_table(result)
    sys.stdout.write(text)
    write_export(export_path, text)
    return EXIT_OK


def cmd_chrono_add(args: argparse.Namespace) -> int:
    missing_options(
        (
            ("--voyage", args.voyage),
            ("--utc", args.utc),
            ("--reading", args.reading),
            ("--error", args.error),
            ("--direction", args.direction),
        ),
        CHRONO_ADD_USAGE,
    )
    voyage_name = str(args.voyage).strip()
    if not voyage_name:
        raise UsageError("--voyage: voyage name must not be empty")
    observed = parse_utc(args.utc)
    reading = parse_reading(args.reading)
    error_value = parse_float(args.error, "--error", "chronometer error")
    direction = str(args.direction).strip().lower()
    if direction not in ("fast", "slow"):
        raise UsageError(
            f"--direction: must be fast or slow (got {args.direction!r})"
        )
    raw_error = str(args.error).strip()
    if raw_error.startswith("-") and direction == "fast":
        raise UsageError(
            "--error: a negative value means the chronometer reads slow, which "
            "conflicts with --direction fast"
        )
    if raw_error.startswith("+") and direction == "slow":
        raise UsageError(
            "--error: a leading plus means the chronometer reads fast, which "
            "conflicts with --direction slow"
        )
    rate: float | None = None
    if args.rate is not None:
        rate = parse_float(args.rate, "--rate", "rate in seconds per day")
        if abs(rate) > 600.0:
            raise UsageError(
                f"--rate: rate must be between -600 and 600 s/day "
                f"(got {args.rate!r})"
            )

    store_path = resolve_store_path(args)
    store, _existed = load_store(store_path)
    voyage = find_voyage(store, voyage_name)
    created = False
    if voyage is None:
        voyage = new_voyage(voyage_name)
        store["voyages"].append(voyage)
        created = True

    entry_id = 1 + max(
        (int(entry.get("id", 0) or 0) for entry in voyage["entries"]), default=0
    )
    entry = {
        "id": entry_id,
        "observed_utc": observed.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "chronometer_reading": reading,
        "error_seconds": round(abs(error_value), 1),
        "error_direction": direction,
        "rate_seconds_per_day": None if rate is None else round(rate, 1),
        "note": str(args.note or "").strip(),
        "recorded_utc": timestamp_now(),
    }
    voyage["entries"].append(entry)
    save_store(store_path, store)

    if created:
        print(f"Created voyage {voyage['id']} ({voyage['name']})")
    print(f"Saved entry {entry_id} for voyage {voyage['name']}")
    print(
        f"  UTC {format_stamp(entry['observed_utc'])}  reading {reading}  "
        f"error {format_error(entry)}"
    )
    if entry["note"]:
        print(f"  note: {entry['note']}")
    print(f"Store: {store_path}")
    print("Next: sextantarc chrono list")
    return EXIT_OK


def select_voyages(
    store: dict, selector: str | None, require_entries: bool
) -> list[dict]:
    voyages = store["voyages"]
    if selector:
        voyage = find_voyage(store, selector)
        if voyage is None:
            names = ", ".join(sorted(v["name"] for v in voyages)) or "none yet"
            raise UsageError(
                f"--voyage: no voyage called {selector!r} in the store "
                f"(voyages: {names})"
            )
        return [voyage]
    if require_entries:
        return [voyage for voyage in voyages if voyage.get("entries")]
    return voyages


def cmd_chrono_list(args: argparse.Namespace) -> int:
    store_path = resolve_store_path(args)
    store, _existed = load_store(store_path)
    voyages = select_voyages(store, args.voyage, require_entries=True)
    sys.stdout.write(render_chrono_list(voyages, store_path))
    return EXIT_OK


def cmd_chrono_show(args: argparse.Namespace) -> int:
    store_path = resolve_store_path(args)
    store, _existed = load_store(store_path)
    voyages = select_voyages(store, args.voyage, require_entries=False)
    if not voyages:
        print(f"No voyages yet in {store_path}.")
        print("Create one with: sextantarc voyage new --name \"Cape Horn 2024\"")
        return EXIT_OK
    blocks = [render_chrono_show(voyage) for voyage in voyages]
    sys.stdout.write("\n".join(blocks))
    return EXIT_OK


def cmd_voyage_new(args: argparse.Namespace) -> int:
    missing_options((("--name", args.name),), VOYAGE_NEW_USAGE)
    name = str(args.name).strip()
    if not name:
        raise UsageError("--name: voyage name must not be empty")
    store_path = resolve_store_path(args)
    store, _existed = load_store(store_path)
    existing = find_voyage(store, name)
    if existing is not None:
        raise UsageError(
            f"voyage {existing['name']!r} already exists as {existing['id']}; "
            "choose another name"
        )
    slug = slugify(name)
    if any(voyage["id"] == slug for voyage in store["voyages"]):
        raise UsageError(
            f"voyage id {slug} is already taken; choose another name"
        )
    voyage = new_voyage(name, str(args.notes or ""))
    store["voyages"].append(voyage)
    save_store(store_path, store)
    print(f"Created voyage {voyage['id']} ({voyage['name']})")
    print(
        "Next: sextantarc chrono add --voyage "
        f"\"{voyage['name']}\" --utc 2024-06-01T12:00:00Z "
        "--reading 12:00:12.5"
    )
    return EXIT_OK


def cmd_voyage_list(args: argparse.Namespace) -> int:
    store_path = resolve_store_path(args)
    store, _existed = load_store(store_path)
    sys.stdout.write(render_voyage_list(store["voyages"], store_path))
    return EXIT_OK

# ---------------------------------------------------------------------------
# Command line surface (F4)
# ---------------------------------------------------------------------------

TOP_LEVEL_COMMANDS = ("reduce", "meridian", "chrono", "voyage")

EPILOG = """examples:
  sextantarc reduce --gha 30 --dec "10°00.0'N" --lat "40°00.0'N" \\
      --lon 0 --ho "49°40.0'"
  sextantarc meridian --body sun --limb lower --lat "40°00.0'N" \\
      --dec "10°00.0'N" --hs "59°45.0'" --height-of-eye 3
  sextantarc voyage new --name "Cape Horn 2024" --notes "Falmouth to Ushuaia"
  sextantarc chrono add --voyage "Cape Horn 2024" --utc 2024-06-01T12:00:00Z \\
      --reading 12:00:12.5 --error +12.5 --direction fast \\
      --note "first comparison"
  sextantarc chrono list
  sextantarc chrono show --voyage "Cape Horn 2024"
  sextantarc --self-test

exit codes:
  0  success
  2  usage or validation error (missing or out-of-range flag)
  3  storage error (store file unreadable or unwritable)

store:
  chronometer notes live in ~/.sextantarc/store.json unless --file PATH is given
"""

DESCRIPTION = f"""{APP_NAME} {APP_VERSION} - offline celestial navigation worksheets.

Reduce a sight from almanac GHA and declination, correct a meridian altitude
to a latitude, and keep a per-voyage chronometer error and rate log. Angles
are written as degrees and decimal minutes, e.g. 45°12.6'N, "45 12.6 N" or
-45.21. Nothing is computed from a date: GHA and Dec always come from the
almanac, and every timestamp is UTC."""


class CommandParser(argparse.ArgumentParser):
    """ArgumentParser that points at --help instead of dumping bare usage."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"{PROG}: error: {message}", file=sys.stderr)
        print(f"hint: run '{PROG} --help' for the full command list", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _add_store_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--file",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="store file to use instead of ~/.sextantarc/store.json",
    )


def _add_export_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--export",
        metavar="PATH",
        default=None,
        help="also write the worksheet to PATH as plain text",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite PATH when --export is given",
    )


def _group_handler(group_parser: argparse.ArgumentParser, missing: str) -> Callable:
    def handler(_args: argparse.Namespace) -> int:
        print(f"{PROG}: {missing}", file=sys.stderr)
        group_parser.print_help(sys.stderr)
        return EXIT_USAGE

    return handler


def build_parser() -> argparse.ArgumentParser:
    parser = CommandParser(
        prog=PROG,
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{APP_NAME} {APP_VERSION}",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run the built-in offline numeric and storage checks",
    )
    parser.add_argument(
        "--file",
        metavar="PATH",
        default=None,
        help="store file (default ~/.sextantarc/store.json)",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    reduce_parser = subparsers.add_parser(
        "reduce",
        help="reduce one sight to an intercept (worksheet)",
        description="Reduce one sight with the intercept method and print the worksheet.",
        epilog=REDUCE_USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    reduce_parser.add_argument(
        "--body", default="Sun", help="body name for the sheet, e.g. Sun or Vega"
    )
    reduce_parser.add_argument("--gha", metavar="GHA", help="GHA from the almanac, 0-360°")
    reduce_parser.add_argument(
        "--dec", metavar="DEC", help="declination from the almanac, e.g. \"10°00.0'N\""
    )
    reduce_parser.add_argument(
        "--lat", metavar="LAT", help="assumed latitude, e.g. \"40°00.0'N\""
    )
    reduce_parser.add_argument(
        "--lon", metavar="LON", help="assumed longitude, east positive, e.g. \"0°00.0'\""
    )
    reduce_parser.add_argument(
        "--ho", metavar="HO", help="observed altitude, already corrected, e.g. \"49°40.0'\""
    )
    _add_export_options(reduce_parser)
    _add_store_option(reduce_parser)
    reduce_parser.set_defaults(handler=cmd_reduce)

    meridian_parser = subparsers.add_parser(
        "meridian",
        help="correct a meridian altitude and derive latitude",
        description="Walk IC, dip, refraction, semi-diameter and parallax, then derive latitude.",
        epilog=MERIDIAN_USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    meridian_parser.add_argument(
        "--body",
        default="sun",
        help="sun, moon, planet or star (default sun)",
    )
    meridian_parser.add_argument(
        "--limb",
        default=None,
        help="lower or upper limb; use none for a star or planet",
    )
    meridian_parser.add_argument(
        "--lat", metavar="LAT", help="DR latitude, e.g. \"40°00.0'N\""
    )
    meridian_parser.add_argument(
        "--dec", metavar="DEC", help="declination from the almanac, e.g. \"10°00.0'N\""
    )
    meridian_parser.add_argument(
        "--hs", metavar="HS", help="sextant altitude, e.g. \"59°45.0'\""
    )
    meridian_parser.add_argument(
        "--ie", metavar="ARCMIN", default=None, help="index correction in arcminutes"
    )
    meridian_parser.add_argument(
        "--height-of-eye", metavar="METRES", default=None, help="height of eye in metres"
    )
    meridian_parser.add_argument(
        "--hp",
        metavar="ARCMIN",
        default=None,
        help="horizontal parallax in arcminutes (required for the Moon)",
    )
    _add_export_options(meridian_parser)
    _add_store_option(meridian_parser)
    meridian_parser.set_defaults(handler=cmd_meridian)

    chrono_parser = subparsers.add_parser(
        "chrono",
        help="record and review chronometer notes per voyage",
        description="Chronometer error and rate notes, kept per voyage in the store file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    chrono_sub = chrono_parser.add_subparsers(dest="action", metavar="<action>")

    add_parser = chrono_sub.add_parser(
        "add",
        help="append a chronometer note to a voyage",
        epilog=CHRONO_ADD_USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_parser.add_argument("--voyage", metavar="NAME", help="voyage name")
    add_parser.add_argument(
        "--utc", metavar="UTC", help="comparison time, 2024-06-01T12:00:00Z"
    )
    add_parser.add_argument(
        "--reading", metavar="HH:MM:SS.s", help="chronometer reading, e.g. 12:00:12.5"
    )
    add_parser.add_argument(
        "--error", metavar="SECONDS", help="error in seconds, e.g. +12.5"
    )
    add_parser.add_argument(
        "--direction", metavar="fast|slow", help="chronometer reading fast or slow"
    )
    add_parser.add_argument(
        "--rate", metavar="SECONDS_PER_DAY", default=None, help="known rate in s/day"
    )
    add_parser.add_argument("--note", metavar="TEXT", default=None, help="free-text note")
    _add_store_option(add_parser)
    add_parser.set_defaults(handler=cmd_chrono_add)

    list_parser = chrono_sub.add_parser(
        "list",
        help="list entries newest first with drift and implied rate",
        description="Entries newest first, with the drift and implied s/day rate since the previous entry.",
    )
    list_parser.add_argument(
        "--voyage", metavar="NAME", default=None, help="limit the list to one voyage"
    )
    _add_store_option(list_parser)
    list_parser.set_defaults(handler=cmd_chrono_list)

    show_parser = chrono_sub.add_parser(
        "show",
        help="print the per-voyage chronometer summary",
        description="Entry count, first and last error, net drift and mean rate, with a warning above 5 s/day.",
    )
    show_parser.add_argument(
        "--voyage", metavar="NAME", default=None, help="summarise one voyage"
    )
    _add_store_option(show_parser)
    show_parser.set_defaults(handler=cmd_chrono_show)

    chrono_parser.set_defaults(
        handler=_group_handler(
            chrono_parser, "chrono needs an action: add, list or show"
        )
    )

    voyage_parser = subparsers.add_parser(
        "voyage",
        help="create and list voyages",
        description="Voyages group the chronometer notes for one passage.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    voyage_sub = voyage_parser.add_subparsers(dest="action", metavar="<action>")

    new_parser = voyage_sub.add_parser(
        "new",
        help="create a voyage record",
        epilog=VOYAGE_NEW_USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    new_parser.add_argument("--name", metavar="NAME", help="voyage name")
    new_parser.add_argument(
        "--notes", metavar="TEXT", default=None, help="free-text notes"
    )
    _add_store_option(new_parser)
    new_parser.set_defaults(handler=cmd_voyage_new)

    voyages_parser = voyage_sub.add_parser(
        "list",
        help="list voyages with created date and entry counts",
        description="One line per voyage with its id, created date and chronometer entry count.",
    )
    _add_store_option(voyages_parser)
    voyages_parser.set_defaults(handler=cmd_voyage_list)

    voyage_parser.set_defaults(
        handler=_group_handler(voyage_parser, "voyage needs an action: new or list")
    )
    return parser


def _configure_streams() -> None:
    """Keep the degree and prime glyphs from crashing a legacy console."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            continue


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    _configure_streams()
    parser = build_parser()

    if arguments and not arguments[0].startswith("-"):
        if arguments[0] not in TOP_LEVEL_COMMANDS:
            print(
                f"{PROG}: unknown command {arguments[0]}, see below",
                file=sys.stderr,
            )
            print("", file=sys.stderr)
            parser.print_help(sys.stderr)
            return EXIT_USAGE

    try:
        args = parser.parse_args(arguments)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE

    if getattr(args, "self_test", False):
        return run_self_test()

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stdout)
        return EXIT_OK

    try:
        return handler(args)
    except UsageError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except StorageError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return EXIT_STORAGE
    except BrokenPipeError:
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
