"""Tests for SextantArc: sight reduction, meridian correction, chrono store.

Every test drives the tool the way a navigator does - through main() and the
real store file - so the numerics, the exit codes and the store safety rules
are all covered. Nothing touches ~/.sextantarc: --file points at a temp dir.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sextantarc

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOL = PROJECT_ROOT / "sextantarc.py"


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    return tmp_path / "store.json"


def run(*args: str, store_file: Path | None = None) -> int:
    argv = list(args)
    if store_file is not None:
        argv = ["--file", str(store_file), *argv]
    return sextantarc.main(argv)


def cli(*args: str, store_file: Path | None = None) -> subprocess.CompletedProcess:
    argv = [sys.executable, str(TOOL)]
    if store_file is not None:
        argv += ["--file", str(store_file)]
    argv += list(args)
    return subprocess.run(argv, capture_output=True, text=True, timeout=30)


# ---------------------------------------------------------------------------
# T1 - shell: version, help, exit codes
# ---------------------------------------------------------------------------


def test_version_prints_name_and_version(capsys):
    assert sextantarc.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "SextantArc 0.1.0"


def test_bare_invocation_prints_help_and_exits_zero(capsys):
    assert sextantarc.main([]) == 0
    out = capsys.readouterr().out
    for command in ("reduce", "meridian", "chrono", "voyage"):
        assert command in out
    assert "--self-test" in out
    assert "exit codes" in out


def test_unknown_command_names_the_command_and_exits_two(capsys):
    assert sextantarc.main(["reduse", "--gha", "30"]) == 2
    err = capsys.readouterr().err
    assert "unknown command reduse" in err
    assert "--help" in err


def test_chrono_without_action_explains_and_exits_two(capsys):
    assert sextantarc.main(["chrono"]) == 2
    assert "add, list or show" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# T2 - angle parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,kind,expected",
    [
        ("45°12.6'N", "lat", 45.21),
        ("45 12.6 N", "lat", 45.21),
        ("-45.21", "lat", -45.21),
        ("33°00.0'S", "lat", -33.0),
        ("120°30.0'E", "lon", 120.5),
        ("120°30.0'W", "lon", -120.5),
        ("10°00.0'N", "dec", 10.0),
        ("10\u00b000.0'S", "dec", -10.0),
        ("30", "gha", 30.0),
        ("030°00.0'", "gha", 30.0),
        ("49°40.0'", "ho", 49 + 40 / 60),
    ],
)
def test_parse_angle_accepts_documented_shapes(raw, kind, expected):
    assert sextantarc.parse_angle(raw, kind, "--flag") == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize(
    "raw,kind,flag,fragment",
    [
        ("95°N", "lat", "--lat", "latitude must be between 0 and 90 degrees"),
        ("400", "gha", "--gha", "GHA must be between 0 and 360 degrees"),
        ("95°00.0'", "ho", "--ho", "observed altitude must be between 0 and 90 degrees"),
        ("10°N", "lon", "--lon", "longitude takes a E or W hemisphere letter"),
        ("10°E", "gha", "--gha", "no hemisphere letter"),
        ("45°61.0'N", "lat", "--lat", "minutes must be below 60"),
        ("north", "lat", "--lat", "cannot read"),
    ],
)
def test_parse_angle_rejects_bad_input(raw, kind, flag, fragment):
    with pytest.raises(sextantarc.UsageError) as excinfo:
        sextantarc.parse_angle(raw, kind, flag)
    assert fragment in str(excinfo.value)


def test_format_round_trip_uses_degree_minute_hemisphere():
    assert sextantarc.format_position(45.21, "lat") == "45° 12.6′ N"
    assert sextantarc.format_dm(30.0) == "30°00.0′"


# ---------------------------------------------------------------------------
# T3 - sight reduction
# ---------------------------------------------------------------------------


def test_reduce_known_sight_numbers():
    sight = sextantarc.reduce_sight("Sun", 30.0, 10.0, 40.0, 0.0, 49 + 40 / 60)
    assert sight.lha_deg == pytest.approx(30.0, abs=1e-9)
    assert sight.hc_deg == pytest.approx(49.9029, abs=0.005)
    assert sight.zn_deg == pytest.approx(229.9, abs=1.0)
    assert sight.intercept_nm == pytest.approx(-14.2, abs=0.2)
    assert sight.toward_away == "Away"


def test_reduce_worksheet_echoes_inputs_and_numbers_every_step(capsys):
    assert (
        run(
            "reduce",
            "--gha", "30",
            "--dec", "10°00.0'N",
            "--lat", "40°00.0'N",
            "--lon", "0",
            "--ho", "49°40.0'",
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "45° 12.6′ N" not in out
    assert "40° 00.0′ N" in out
    assert "10° 00.0′ N" in out
    assert "LHA = 30°00.0′" in out
    assert "Hc = 49°54.2′" in out
    assert "Zn = 360° - Z = 229.9°" in out
    for step in ("1.", "2.", "3.", "4."):
        assert step in out
    assert "intercept 14.2 nm Away" in out
    assert max(len(line) for line in out.splitlines()) <= 80


def test_reduce_toward_when_observed_is_higher():
    sight = sextantarc.reduce_sight("Sun", 30.0, 10.0, 40.0, 0.0, 50.0)
    assert sight.intercept_nm > 0
    assert sight.toward_away == "Toward"


def test_reduce_lha_normalises_across_360():
    sight = sextantarc.reduce_sight("Sun", 350.0, 10.0, 40.0, 20.0, 40.0)
    assert 0.0 <= sight.lha_deg < 360.0
    assert sight.lha_deg == pytest.approx(10.0)


def test_reduce_rejects_out_of_range_observed_altitude(capsys):
    assert (
        run(
            "reduce",
            "--gha", "30",
            "--dec", "10°00.0'N",
            "--lat", "40°00.0'N",
            "--lon", "0",
            "--ho", "95°00.0'",
        )
        == 2
    )
    assert "observed altitude must be between 0 and 90 degrees" in capsys.readouterr().err


def test_reduce_without_flags_lists_the_flags_and_an_example(capsys):
    assert run("reduce") == 2
    err = capsys.readouterr().err
    assert "--gha" in err and "--ho" in err
    assert "sextantarc reduce" in err


# ---------------------------------------------------------------------------
# T4 - meridian altitude correction
# ---------------------------------------------------------------------------


def test_meridian_sun_lower_limb_table_and_latitude(capsys):
    assert (
        run(
            "meridian",
            "--body", "sun",
            "--limb", "lower",
            "--lat", "40°00.0'N",
            "--dec", "10°00.0'N",
            "--hs", "59°45.0'",
            "--ie", "0",
            "--height-of-eye", "3",
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "Dip (height of eye 3 m)" in out
    assert "-3.0′" in out
    assert "-0.6′" in out
    assert "+16.0′" in out
    assert "+0.1′" in out
    assert "59°57.4′" in out
    assert "40° 02.6′ N" in out
    assert "same name" in out
    assert "bears south" in out


def test_meridian_corrections_match_the_walk():
    result = sextantarc.correct_meridian_altitude(
        "sun", "lower", 40.0, 10.0, 59.75, 0.0, 3.0, None
    )
    assert result.dip_arcmin == pytest.approx(-3.05, abs=0.01)
    assert result.refraction_arcmin == pytest.approx(-0.58, abs=0.03)
    assert result.semi_diameter_arcmin == pytest.approx(16.0, abs=0.01)
    assert result.parallax_arcmin == pytest.approx(0.076, abs=0.01)
    assert result.ho_corrected_deg == pytest.approx(59.9574, abs=0.02)
    assert result.lat_result_deg == pytest.approx(40.0426, abs=0.02)


def test_meridian_upper_limb_subtracts_semi_diameter():
    lower = sextantarc.correct_meridian_altitude(
        "sun", "lower", 40.0, 10.0, 45.0, 0.0, 3.0, None
    )
    upper = sextantarc.correct_meridian_altitude(
        "sun", "upper", 40.0, 10.0, 45.0, 0.0, 3.0, None
    )
    assert upper.semi_diameter_arcmin == pytest.approx(-16.0, abs=0.01)
    assert upper.ho_corrected_deg < lower.ho_corrected_deg


def test_meridian_moon_parallax_comes_from_hp(capsys):
    result = sextantarc.correct_meridian_altitude(
        "moon", "lower", 40.0, 10.0, 59.75, 0.0, 3.0, 59.0
    )
    apparent = result.hs_deg + result.index_correction_arcmin / 60.0 + result.dip_arcmin / 60.0
    expected = 59.0 * math.cos(math.radians(apparent))
    assert result.parallax_arcmin == pytest.approx(expected, abs=1e-9)
    assert 29.0 < result.parallax_arcmin < 30.5
    assert 15.9 < result.semi_diameter_arcmin < 16.3


def test_meridian_requires_hp_for_the_moon(capsys):
    assert (
        run("meridian", "--body", "moon", "--lat", "40N", "--dec", "10N", "--hs", "59")
        == 2
    )
    assert "--hp" in capsys.readouterr().err


def test_meridian_rejects_limb_on_a_star(capsys):
    assert (
        run("meridian", "--body", "star", "--limb", "lower",
            "--lat", "40N", "--dec", "10N", "--hs", "59")
        == 2
    )
    assert "no semi-diameter" in capsys.readouterr().err


def test_meridian_contrary_name_rule_line():
    result = sextantarc.correct_meridian_altitude(
        "sun", "lower", 40.0, -10.0, 45.0, 0.0, 3.0, None
    )
    assert "contrary name" in result.rule_line


# ---------------------------------------------------------------------------
# T5 - store: empty, corrupt, atomic
# ---------------------------------------------------------------------------


def test_chrono_list_without_a_store_creates_nothing(store, capsys):
    assert run("chrono", "list", store_file=store) == 0
    out = capsys.readouterr().out
    assert "No chronometer entries yet" in out
    assert "sextantarc chrono add" in out
    assert not store.exists()


def test_corrupt_store_is_reported_and_left_alone(store, capsys):
    store.write_text('{"voyages": [', encoding="utf-8")
    original = store.read_text(encoding="utf-8")
    assert run("chrono", "list", store_file=store) == 3
    err = capsys.readouterr().err
    assert "invalid JSON" in err
    assert "nothing was overwritten" in err
    assert str(store) in err
    assert store.read_text(encoding="utf-8") == original


def test_store_is_written_atomically_with_mode_0600(store):
    assert run("voyage", "new", "--name", "Bermuda Run", store_file=store) == 0
    assert (store.stat().st_mode & 0o777) == 0o600
    payload = json.loads(store.read_text(encoding="utf-8"))
    assert payload["app"] == "SextantArc"
    assert payload["schema_version"] == 1
    assert [voyage["id"] for voyage in payload["voyages"]] == ["bermuda-run"]


# ---------------------------------------------------------------------------
# T6 - chrono add
# ---------------------------------------------------------------------------


def _add(store: Path, *extra: str) -> int:
    return run(
        "chrono", "add",
        "--voyage", "Cape Horn 2024",
        "--utc", "2024-06-01T12:00:00Z",
        "--reading", "12:00:12.5",
        "--error", "+12.5",
        "--direction", "fast",
        *extra,
        store_file=store,
    )


def test_chrono_add_saves_and_list_shows_it(store, capsys):
    assert _add(store, "--note", "first comparison") == 0
    out = capsys.readouterr().out
    assert "Saved entry 1 for voyage Cape Horn 2024" in out
    assert "Created voyage cape-horn-2024" in out
    assert "12.5 s fast" in out

    assert run("chrono", "list", store_file=store) == 0
    listing = capsys.readouterr().out
    assert "12.5 s fast" in listing
    assert "first comparison" in listing


def test_chrono_add_rejects_a_bad_utc_without_writing(store, capsys):
    argv = [
        "chrono", "add",
        "--voyage", "Cape Horn 2024",
        "--utc", "yesterday",
        "--reading", "12:00:12.5",
        "--error", "+12.5",
        "--direction", "fast",
    ]
    assert run(*argv, store_file=store) == 2
    assert "UTC time must look like 2024-06-01T12:00:00Z" in capsys.readouterr().err
    assert not store.exists()


def test_chrono_add_rejects_conflicting_sign_and_direction(store, capsys):
    argv = [
        "chrono", "add",
        "--voyage", "Cape Horn 2024",
        "--utc", "2024-06-01T12:00:00Z",
        "--reading", "12:00:12.5",
        "--error", "-12.5",
        "--direction", "fast",
    ]
    assert run(*argv, store_file=store) == 2
    assert "conflicts with --direction fast" in capsys.readouterr().err
    assert not store.exists()


def test_chrono_add_validates_the_reading(store, capsys):
    argv = [
        "chrono", "add",
        "--voyage", "Cape Horn 2024",
        "--utc", "2024-06-01T12:00:00Z",
        "--reading", "12:99",
        "--error", "12.5",
        "--direction", "slow",
    ]
    assert run(*argv, store_file=store) == 2
    assert "chronometer reading must look like HH:MM:SS.s" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# T7 - drift between entries and the voyage summary
# ---------------------------------------------------------------------------


def _seed_two_entries(store: Path) -> None:
    assert _add(store, "--note", "first comparison") == 0
    assert run(
        "chrono", "add",
        "--voyage", "Cape Horn 2024",
        "--utc", "2024-06-03T12:00:00Z",
        "--reading", "12:00:19.0",
        "--error", "18.5",
        "--direction", "fast",
        "--note", "second comparison",
        store_file=store,
    ) == 0


def test_chrono_list_is_newest_first_with_implied_rate(store, capsys):
    _seed_two_entries(store)
    capsys.readouterr()
    assert run("chrono", "list", store_file=store) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    newest = next(index for index, line in enumerate(lines) if "2024-06-03" in line)
    oldest = next(index for index, line in enumerate(lines) if "2024-06-01" in line)
    assert newest < oldest
    assert "+3.0 s/day over 2.0 days" in "\n".join(lines)


def test_chrono_show_summarises_the_voyage(store, capsys):
    _seed_two_entries(store)
    assert run("chrono", "show", "--voyage", "Cape Horn 2024", store_file=store) == 0
    out = capsys.readouterr().out
    assert "Entries   2" in out
    assert "12.5 s fast" in out and "18.5 s fast" in out
    assert "Mean rate +3.0 s/day" in out
    assert "WARNING" not in out


def test_chrono_show_warns_above_five_seconds_per_day(store, capsys):
    assert _add(store) == 0
    assert run(
        "chrono", "add",
        "--voyage", "Cape Horn 2024",
        "--utc", "2024-06-03T12:00:00Z",
        "--reading", "12:00:35.0",
        "--error", "34.5",
        "--direction", "fast",
        store_file=store,
    ) == 0
    assert run("chrono", "show", "--voyage", "Cape Horn 2024", store_file=store) == 0
    out = capsys.readouterr().out
    assert "Mean rate +11.0 s/day" in out
    assert "WARNING" in out
    assert "above 5 s/day" in out


def test_slow_errors_are_signed_negative():
    entry = {"error_seconds": 12.5, "error_direction": "slow"}
    assert sextantarc.signed_error(entry) == -12.5


# ---------------------------------------------------------------------------
# T8 - voyages
# ---------------------------------------------------------------------------


def test_voyage_new_then_list(store, capsys):
    assert run("voyage", "new", "--name", "Cape Horn 2024", store_file=store) == 0
    assert "Created voyage cape-horn-2024 (Cape Horn 2024)" in capsys.readouterr().out
    assert run("voyage", "list", store_file=store) == 0
    listing = capsys.readouterr().out
    assert "cape-horn-2024" in listing
    assert "0 entries" in listing


def test_voyage_new_rejects_a_duplicate_without_touching_the_store(store, capsys):
    assert run("voyage", "new", "--name", "Cape Horn 2024", store_file=store) == 0
    before = store.read_text(encoding="utf-8")
    capsys.readouterr()
    assert run("voyage", "new", "--name", "Cape Horn 2024", store_file=store) == 2
    assert "already exists" in capsys.readouterr().err
    assert store.read_text(encoding="utf-8") == before


def test_voyage_list_empty_state(store, capsys):
    assert run("voyage", "list", store_file=store) == 0
    out = capsys.readouterr().out
    assert "No voyages yet" in out
    assert "sextantarc voyage new" in out


# ---------------------------------------------------------------------------
# T9 - export
# ---------------------------------------------------------------------------


REDUCE_ARGS = (
    "reduce",
    "--gha", "30",
    "--dec", "10°00.0'N",
    "--lat", "40°00.0'N",
    "--lon", "0",
    "--ho", "49°40.0'",
)


def test_export_writes_the_same_text_as_stdout(tmp_path, capsys):
    target = tmp_path / "sheet.txt"
    assert run(*REDUCE_ARGS, "--export", str(target)) == 0
    captured = capsys.readouterr()
    assert target.read_text(encoding="utf-8") == captured.out
    assert str(target) in captured.err


def test_export_refuses_to_overwrite_without_force(tmp_path, capsys):
    target = tmp_path / "sheet.txt"
    target.write_text("old sheet\n", encoding="utf-8")
    assert run(*REDUCE_ARGS, "--export", str(target)) == 2
    assert "sheet.txt exists; pass --force to overwrite" in capsys.readouterr().err
    assert target.read_text(encoding="utf-8") == "old sheet\n"

    assert run(*REDUCE_ARGS, "--export", str(target), "--force") == 0
    assert "SextantArc" in target.read_text(encoding="utf-8")


def test_meridian_export(tmp_path, capsys):
    target = tmp_path / "meridian.txt"
    assert (
        run(
            "meridian",
            "--lat", "40°00.0'N",
            "--dec", "10°00.0'N",
            "--hs", "59°45.0'",
            "--height-of-eye", "3",
            "--export", str(target),
        )
        == 0
    )
    assert "meridian altitude correction table" in target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# T10 - storage failure handling
# ---------------------------------------------------------------------------


def test_unwritable_store_reports_the_path_and_exits_three(tmp_path, capsys, monkeypatch):
    store = tmp_path / "store.json"

    def explode(*_args, **_kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(sextantarc.os, "replace", explode)
    assert run("voyage", "new", "--name", "Cape Horn 2024", store_file=store) == 3
    err = capsys.readouterr().err
    assert "Could not write" in err
    assert "Read-only file system" in err
    assert "left untouched" in err


def test_unreadable_store_exits_three(tmp_path, capsys, monkeypatch):
    store = tmp_path / "store.json"
    store.write_text("{}", encoding="utf-8")

    def explode(*_args, **_kwargs):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(sextantarc.Path, "read_text", explode)
    assert run("chrono", "list", store_file=store) == 3
    assert "Permission denied" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# T11 - self-test
# ---------------------------------------------------------------------------


def test_self_test_passes_offline(capsys):
    assert sextantarc.main(["--self-test"]) == 0
    out = capsys.readouterr().out
    assert out.count("PASS") == 3
    assert "3/3 checks passed" in out


def test_self_test_reports_one_failure_and_exits_one(capsys, monkeypatch):
    monkeypatch.setitem(sextantarc.SIGHT_EXPECTATION, "hc_deg", 12.0)
    assert sextantarc.main(["--self-test"]) == 1
    out = capsys.readouterr().out
    assert "FAIL  sight reduction" in out
    assert "expected 12°00.0′" in out
    assert "PASS  meridian correction" in out
    assert "PASS  store round-trip" in out
    assert "2/3 checks passed" in out


# ---------------------------------------------------------------------------
# End to end: the documented command line really works
# ---------------------------------------------------------------------------


def test_cli_end_to_end_reduce(tmp_path):
    result = cli(*REDUCE_ARGS, "--export", str(tmp_path / "sheet.txt"))
    assert result.returncode == 0
    assert "Result: Ho 49°40.0′" in result.stdout
    assert (tmp_path / "sheet.txt").exists()


def test_cli_reports_usage_errors_with_exit_two():
    result = cli(
        "reduce", "--gha", "400", "--dec", "10N", "--lat", "40N",
        "--lon", "0", "--ho", "40",
    )
    assert result.returncode == 2
    assert "--gha: GHA must be between 0 and 360 degrees" in result.stderr


# ---------------------------------------------------------------------------
# Committed design direction - it lives in the single deliverable file
# ---------------------------------------------------------------------------


def test_design_direction_is_committed_in_the_single_file():
    tokens = sextantarc.DESIGN_TOKENS
    assert tokens["--accent"] == "#007eb7"
    assert tokens["--ring"] == "#007eb7"
    assert tokens["--background"] == "#f7fbfe"
    assert tokens["--foreground"] == "#0e1318"
    assert tokens["--border"] == "#dbdfe2"
    assert tokens["--primary"] == "#182127"
    assert tokens["--destructive"] == "#c9302d"
    assert len(tokens) == 16
    assert "Space Mono" in sextantarc.DESIGN_FONTS["heading"]
    assert "Work Sans" in sextantarc.DESIGN_FONTS["body"]


def test_design_root_css_is_one_balanced_block_with_every_token():
    css = sextantarc.DESIGN_ROOT_CSS
    assert css.startswith(
        "@import url('https://fonts.googleapis.com/css2?"
        "family=Space+Mono:wght@400;700&family=Work+Sans:wght@400;500;700&display=swap');"
    )
    assert css.count(":root {") == 1
    assert css.count("{") == css.count("}") == 1
    for token, value in sextantarc.DESIGN_TOKENS.items():
        assert f"{token}: {value};" in css
    assert "--font-heading: 'Space Mono'" in css
    assert "--font-body: 'Work Sans'" in css


def test_the_measure_token_drives_the_printed_width():
    assert sextantarc.DESIGN_MEASURE == 78
    assert sextantarc.wrap_hanging.__defaults__ == (sextantarc.DESIGN_MEASURE,)


def test_no_assets_ship_beside_the_single_file():
    root = Path(__file__).resolve().parent.parent
    assert not list(root.rglob("*.css"))
    assert not list(root.rglob("*.js"))
    assert not (root / "static").exists()
