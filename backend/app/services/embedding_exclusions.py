"""
Hardcoded per-cell embedding exclusions — Phase 7 follow-up (cross-file
solution leak fix).

Each entry is a confirmed (unsolved_file_id, cell_index) pair from the
phase-7.7 closeout audit (docs/phase7-known-gaps-record.txt, "Sub-feature
7.7" section) whose content is either a full or partial solution to a
DIFFERENT unsolved file's graded gap. This is a corpus-specific fix, not a
general classifier — see that gap-record entry for why a general mechanism
was ruled out (a 1-5-instructor project where new content is added by the
same people who found this bug; an automated "detect any future cross-file
leak" system has no way to be verified as actually working).

Adding a new file/session later does NOT automatically get this protection.
A human must manually check any new lecture/assignment upload that carries
content forward from a prior week against other files' graded gaps before
upload — recorded as a permanent process gap, not solved here.

Keyed on unsolved_file_id (a live DB primary key), not filename+session —
a deliberate simplicity trade-off for this project's scale, confirmed at
sign-off. If the dev DB is ever reset and these rows re-seeded differently,
this list would need to be re-derived against the new ids.
"""

# (unsolved_file_id, cell_index) -> human-readable reason, purely for
# logging/auditability; the tuple itself is what's checked.
EXCLUDED_CELLS: dict[tuple[int, int], str] = {
    (17, 12): (
        "Week10_Day2.ipynb (session 5) — GIVEN 'Appendix solution' run_agent, "
        "full solution to week10_day1_lab.ipynb id=21 cell 22's graded gap."
    ),
    (17, 21): (
        "Week10_Day2.ipynb (session 5) — run_agent_v2, a second unlabelled "
        "full copy of the same solution."
    ),
    (19, 12): (
        "Week10_Day2.ipynb.ipynb (session 6) — same GIVEN run_agent solution, "
        "byte-identical to id=17 cell 12."
    ),
    (19, 21): (
        "Week10_Day2.ipynb.ipynb (session 6) — same run_agent_v2 copy as id=17 cell 21."
    ),
    (22, 12): (
        "Week10_Day2.ipynb (session 7) — same GIVEN run_agent solution; shares "
        "a session with the gap it solves (week10_day1_lab.ipynb id=21)."
    ),
    (22, 21): (
        "Week10_Day2.ipynb (session 7) — same run_agent_v2 copy."
    ),
    (21, 15): (
        "week10_day1_lab.ipynb (session 7) — GIVEN get_weather fake-data cell; "
        "gives 2 of 3 city values for Week 10_Lab3.ipynb id=20 cell 10's weather-dict gap."
    ),
    (21, 22): (
        "week10_day1_lab.ipynb (session 7) — the run_agent gap cell's own scaffold "
        "line ('tool_registry = {\"get_weather\": get_weather}') gives half of "
        "Week 10_Lab3.ipynb id=20 cell 25's tool_registry gap."
    ),
    (21, 27): (
        "week10_day1_lab.ipynb (session 7) — the calculate() spec cell's "
        "'hint: Python's eval() works fine' gives away Week 10_Lab3.ipynb "
        "id=20 cell 15's calculator gap."
    ),
}


def is_excluded_from_embedding(unsolved_file_id: int, cell_index: int) -> bool:
    """True if this cell is a confirmed cross-file solution leak and must
    never be embedded, regardless of how the caller otherwise treats it."""
    return (unsolved_file_id, cell_index) in EXCLUDED_CELLS
