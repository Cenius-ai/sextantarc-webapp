# Using SextantArc

Every command is flags only - nothing prompts, so a run is scriptable and can
be pasted into a log. Angles accept `45°12.6'N`, `45 12.6 N` and `-45.21`;
latitude takes N/S, longitude E/W, GHA 0–360° and declination 0–90°.

Exit codes: `0` ok, `1` a `--self-test` check failed, `2` usage/validation,
`3` storage error.

---

## 1. Reduce a sight to an intercept

You read GHA and declination from the almanac, correct the observed altitude
on paper, and let SextantArc do the spherical trigonometry:

```
python3 sextantarc.py reduce \
    --body Sun \
    --gha 30 \
    --dec "10°00.0'N" \
    --lat "40°00.0'N" \
    --lon 0 \
    --ho "49°40.0'"
```

What you get, in order:

1. an echo line for every parsed input, so a transposed digit is visible
   before you trust the numbers (`45°12.6'N` comes back as `45° 12.6′ N`);
2. step 1 - LHA = GHA + assumed longitude, normalised to 0–360°;
3. step 2 - `sin Hc = sin Lat sin Dec + cos Lat cos Dec cos LHA` with the
   value of sin Hc printed beside Hc;
4. step 3 - the azimuth angle Z and the `LHA < 180 -> 360 - Z` rule that
   turns it into Zn;
5. step 4 - the intercept `(Ho - Hc) x 60` in nautical miles, labelled
   Toward or Away with the reason in words;
6. a one-line result you can copy into the plot.

Assumed longitude is east positive: `--lon "120°30.0'W"` is `-120.5`.
Longitude past 180° is rejected, so use the hemisphere letter.

Write the same text to the ship's records:

```
python3 sextantarc.py reduce --gha 30 --dec "10°00.0'N" --lat "40°00.0'N" \
    --lon 0 --ho "49°40.0'" --export sight-sheet.txt
```

`--export` writes exactly what went to stdout and prints the absolute path.
An existing file is refused with exit 2 - `sheet.txt exists; pass --force to
overwrite` - and left untouched until you pass `--force`.

## 2. Correct a meridian altitude and derive latitude

```
python3 sextantarc.py meridian \
    --body sun --limb lower \
    --lat "40°00.0'N" --dec "10°00.0'N" \
    --hs "59°45.0'" --ie 0 --height-of-eye 3
```

The table walks each correction in arcminutes with a running total:

| Row | Sign | Source |
|---|---|---|
| Index correction | as given | `--ie` (on/off the arc) |
| Dip | negative | `-1.76 x sqrt(height of eye in metres)` |
| Refraction | negative | Bennett: `-1 / tan(h + 7.31/(h + 4.4))` |
| Semi-diameter | + lower, − upper | Sun 16.0′ mean; Moon `sin SD = 0.2725 sin HP` |
| Parallax in altitude | positive | Sun 0.15′ x cos Ho; Moon `HP cos Ho`; stars 0 |

Then the latitude: `90° - Ho` gives the zenith distance, and the rule line
states which case it applied - same or contrary name, body bearing north or
south - before printing `Lat = Dec ± (90° - Ho)` and the resulting latitude
with its distance from the DR position.

Notes:

- `--body moon` requires `--hp` (the day's horizontal parallax in
  arcminutes): the Moon's parallax is too large to assume.
- `--body star` and `--body planet` use `--limb none`; they show no
  semi-diameter.
- An index error *on* the arc is positive: `--ie -2.5` for 2.5′ off the arc.
- `--export PATH` works here too.

## 3. Keep a chronometer log per voyage

Create the voyage, then append one note per comparison:

```
python3 sextantarc.py voyage new --name "Cape Horn 2024" --notes "Falmouth to Ushuaia"

python3 sextantarc.py chrono add \
    --voyage "Cape Horn 2024" \
    --utc 2024-06-01T12:00:00Z \
    --reading 12:00:12.5 \
    --error +12.5 --direction fast \
    --note "first comparison, Portsmouth"

python3 sextantarc.py chrono add \
    --voyage "Cape Horn 2024" \
    --utc 2024-06-03T12:00:00Z \
    --reading 12:00:19.0 \
    --error 18.5 --direction fast \
    --note "second comparison, Biscay"
```

- `--voyage` matches the slug or the name, and creates the voyage if it is
  new, so a single `chrono add` works on a fresh store.
- `--utc` must be `YYYY-MM-DDTHH:MM:SSZ`; anything else exits 2 before the
  store is written.
- `--error` is seconds with `--direction fast|slow`. A leading sign must
  agree with the direction: `-12.5 --direction fast` is rejected.
- `--rate` is optional and free-form (a rate you already know).

Read the log:

```
python3 sextantarc.py chrono list
```

Newest first, with the drift since the previous entry and the implied rate,
e.g. `since previous: +6.0 s, +3.0 s/day over 2.0 days`. `--voyage NAME`
limits it to one voyage. With no store file at all it prints the empty state
and creates nothing.

Summarise:

```
python3 sextantarc.py chrono show --voyage "Cape Horn 2024"
```

Count, first and last error with their UTC stamps, elapsed days, net drift,
mean rate, and a `WARNING` line when the rate exceeds 5 s/day. Without
`--voyage` every voyage gets its own block.

```
python3 sextantarc.py voyage list
```

One line per voyage: id, created date, name and chronometer entry count.

## 4. The store file

Default `~/.sextantarc/store.json`, override anywhere with `--file PATH`
(before the subcommand: `sextantarc --file /media/usb/store.json chrono list`).

```
{
  "schema_version": 1,
  "app": "SextantArc",
  "app_version": "0.1.0",
  "voyages": [
    {
      "id": "cape-horn-2024",
      "name": "Cape Horn 2024",
      "created_utc": "2024-05-30T09:12:04Z",
      "notes": "Falmouth to Ushuaia",
      "entries": [
        {
          "id": 1,
          "observed_utc": "2024-06-01T12:00:00Z",
          "chronometer_reading": "12:00:12.5",
          "error_seconds": 12.5,
          "error_direction": "fast",
          "rate_seconds_per_day": null,
          "note": "first comparison, Portsmouth",
          "recorded_utc": "2024-06-01T12:05:11Z"
        }
      ]
    }
  ]
}
```

The log is append-only: there is no edit or delete command, so a voyage
record stays auditable. Every write is a temp file plus `os.replace` at mode
0600. If the file is not valid JSON the tool prints

```
sextantarc: Could not read <path>: invalid JSON — fix or move this file; nothing was overwritten
```

and exits 3 without touching a byte of it.

## 5. Verify the tool offline

```
python3 sextantarc.py --self-test
```

```
  PASS  sight reduction: Hc 49°54.2′ (expected 49°54.1′), Zn 229.9° (expected 229.9°), intercept 14.2 nm Away (expected 14.1 nm Away)
  PASS  meridian correction: Ho 59°57.4′ (expected 59°57.4′), latitude 40° 02.6′ N (expected 40° 02.6′ N)
  PASS  store round-trip: wrote and read back 1 voyage with 1 entry, file mode 0600

3/3 checks passed
```

Exit 0 when all three pass, exit 1 when any fails (the failing line shows the
expected and the actual number, and the other checks still report).

## 6. Worked example: one noon sight

1. Meridian passage at 12:04:11 UTC. Sextant altitude of the Sun's lower
   limb: 59°45.0′. Index error 0.0′, height of eye 3 m.
2. Almanac: Dec 10°00.0′ N (same name as the DR latitude, so the body bears
   south).
3. `meridian --body sun --limb lower --lat "40°00.0'N" --dec "10°00.0'N"
   --hs "59°45.0'" --height-of-eye 3` gives Ho 59°57.4′ and latitude
   40° 02.6′ N - 2.6′ north of the DR position, which is what you plot.
4. Later that afternoon, read a GHA/Dec pair from the almanac and run
   `reduce` for the intercept; the plot of one noon latitude plus one
   intercept is the fix.
