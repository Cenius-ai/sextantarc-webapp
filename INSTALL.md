# Installing SextantArc

SextantArc is one file of Python. There is no build step, no database and no
service. Follow the steps in order.

## 1. Prerequisites (installed by you, once)

| Need | Why | Check |
|---|---|---|
| Python 3.9+ | runs `sextantarc.py` | `python3 --version` |
| pip (bundled with Python) | only for `install.sh` / the test suite | `python3 -m pip --version` |

Nothing else is required: no compiler, no `libsqlite3-dev`, no Node, no
system packages. Do not run `apt-get`, `brew` or `sudo` for this project.

## 2. Install the project dependencies (one command, then it exits)

```
bash install.sh
```

That script, in order:

1. `python3 -m pip install --upgrade pip setuptools wheel`
2. `python3 -m pip install -r requirements.txt` (pytest, for the test suite)
3. `python3 -m pip install -e .` (installs the `sextantarc` console script)
4. Imports the module and prints its version as a self-check

It is safe to re-run, it installs only project-level Python packages, and it
finishes by printing the run commands - it never starts a server or blocks.

If you would rather not install anything at all, skip this step: running
`python3 sextantarc.py ...` needs nothing beyond the standard library.

## 3. Seed data

There is no seed step and no demo account: SextantArc is a single-user
offline tool with no login. The chronometer store is created on the first
`chrono add` or `voyage new`; until then `chrono list` prints an empty state
with the exact command to copy.

To fill a store with a realistic three-entry voyage in a scratch file:

```
python3 sextantarc.py --file /tmp/demo-store.json voyage new --name "Cape Horn 2024" --notes "Falmouth to Ushuaia"
python3 sextantarc.py --file /tmp/demo-store.json chrono add --voyage "Cape Horn 2024" --utc 2024-06-01T12:00:00Z --reading 12:00:12.5 --error +12.5 --direction fast --note "first comparison, Portsmouth"
python3 sextantarc.py --file /tmp/demo-store.json chrono add --voyage "Cape Horn 2024" --utc 2024-06-03T12:00:00Z --reading 12:00:19.0 --error +18.5 --direction fast --note "second comparison, Biscay"
python3 sextantarc.py --file /tmp/demo-store.json chrono add --voyage "Cape Horn 2024" --utc 2024-06-05T12:00:00Z --reading 12:00:26.0 --error +26.0 --direction fast --note "third comparison, westerlies"
```

## 4. Run

```
python3 sextantarc.py --help
```

or, after step 2, simply:

```
sextantarc --help
```

## 5. Verify the install offline

```
python3 sextantarc.py --self-test
```

Expect three PASS lines and `3/3 checks passed`, exit code 0.

## 6. Run the test suite

```
python3 -m pytest -q
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `sextantarc: command not found` | step 2 skipped, or pip installed it outside `PATH` | run `python3 sextantarc.py ...` instead |
| `sextantarc: Could not read <path>: invalid JSON - fix or move this file; nothing was overwritten` | the store file was edited by hand or truncated | restore the file or point `--file` at a new path; nothing was written |
| Degree or prime signs print as `?` | an ancient console codepage | the tool already reconfigures stdout to UTF-8 with replacement; the numbers stay correct |
| Garbled path in the error text | Windows console encoding | same as above; the path is also shown verbatim by `--help --file` |
