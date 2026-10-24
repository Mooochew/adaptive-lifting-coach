"""AI coach notes: a short weekly explanation of the engine's numbers, checked against them.

    program + logs --> compute_metrics (engine) --> facts: plain text, every number the AI may use
                                                       |
                     prompt (app/prompts/coach_notes.py) --> model --> CoachNotes (strict JSON)
                                                       |
      grounding check: every number in the note must appear in the facts, every point must be
      about a lift or muscle in the facts; problems go back to the model (refine.py)
                                                       |
                         still failing or an outage --> no note (the panel says why)

The model explains; it never computes. The engine owns every number, so a note can't promise a
weight, a PR or a trend the logs don't show. Error details go to the server log, never the response.
"""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.exercises import exercise_name, get_exercise
from app.llm import LLMClient, LLMUnavailable
from app.progression import last_ended_week
from app.refine import Checked, refine
from app.schemas import MAX_LOGS, Model, Profile, Program, SetLog
from app.training import compute_metrics

log = logging.getLogger(__name__)

NOTES_DEADLINE_S = 90.0
MIN_CALL_S = 15.0
MAX_POINTS = 4
MAX_HEADLINE = 100
MAX_POINT = 220
MAX_FOCUS = 180
MAX_CHANGES = 60

# Any run of digits, even stuck to letters ("x999", "RPE10", "3x12"), with thousands separators
# ("1,000") and decimals. Not after a digit or a decimal point, so "8.5" is one number.
NUMBER = re.compile(r"(?<![\d.])\d+(?:,\d{3}(?!\d))*(?:\.\d+)?")


def _value(number: str) -> float:
    return float(number.replace(",", ""))


# ---------- What the AI returns (strict JSON schema) ----------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NotePoint(_Strict):
    subject: str          # an exercise id or muscle from the facts, or "overall"
    text: str


class CoachNotes(_Strict):
    headline: str
    points: list[NotePoint]
    focus: str


# ---------- API ----------

class CoachNotesRequest(Model):
    profile: Profile
    program: Program
    logs: list[SetLog] = Field(max_length=MAX_LOGS)
    changes: list[Annotated[str, Field(max_length=200)]] = Field(
        default=[], max_length=MAX_CHANGES,
        description="The plan changes from the last time a week ended (from /program/advance)")
    week: int | None = Field(default=None, ge=1, le=16,
                             description="The week to write about (the one that just ended); default: the latest logged")


class NotePointOut(Model):
    subject: str
    label: str = Field(description="Display name of the subject, e.g. 'Barbell Bench Press' or 'Chest'")
    text: str


class CoachNotesResponse(Model):
    source: Literal["ai", "none"]
    week: int | None = Field(description="The training week the note is about")
    in_progress: bool = Field(description="True when that week isn't finished yet")
    headline: str
    points: list[NotePointOut]
    focus: str
    reason: str | None = Field(description="Why there's no note (safe to show); null when source is 'ai'")
    retryable: bool = Field(description="True when asking again later may give a note (busy, limits, outage)")
    attempts: int = Field(description="Model calls that returned something")


# ---------- Facts: the engine's numbers, as the model sees them ----------

@dataclass
class Facts:
    week: int
    in_progress: bool
    text: str                         # the engine's numbers: the only ones a note may use
    numbers: set[float]
    subjects: dict[str, str]          # id -> display name
    changes: str = ""                 # plan changes sent by the client: context, not checked facts


def _clean(text: str) -> str:
    """Text from the request stays on one line and can't open or close prompt blocks (every tag
    starts with '<'; the engine's own changes use '->', which stays readable)."""
    return " ".join(text.replace("<", "‹").split())


def _fmt(x: float) -> str:
    return f"{x:g}"


def has_working_sets(program: Program, logs: list[SetLog]) -> bool:
    return any(l.block == program.block and l.set_type == "working" for l in logs)


def build_facts(profile: Profile, program: Program, logs: list[SetLog], changes: list[str],
                week: int | None = None) -> Facts | None:
    """`week` (the latest logged up to it; default: the latest logged) as plain-text facts, or None
    when nothing is logged in this block."""
    logs = [l for l in logs if l.block == program.block]
    logged = {l.week for l in logs if l.set_type == "working"}
    if week is not None:
        logged = {w for w in logged if w <= week} or logged
    if not logged:
        return None
    metrics = compute_metrics(profile.goal, program, logs)
    week = max(logged)
    in_progress = week > last_ended_week(program, logs)
    units = program.units
    rx = {(s.day, e.slot_id): e for w in program.weeks if w.week == week for s in w.sessions for e in s.exercises}
    subjects = {"overall": "Overall"}

    lines = [f"week: {week} ({'in progress' if in_progress else 'finished'})",
             f"goal: {profile.goal}; units: {units}", "<lifts>"]
    for lift in metrics.lifts:
        for slot in lift.slots:
            this = next((e for e in slot.exposures if e.week == week), None)
            if this is None:
                continue
            target = rx.get((slot.day, slot.slot_id))
            done = ",".join(str(r) for r in this.reps)
            parts = [f"holds {done} seconds" if get_exercise(lift.exercise_id).timed
                     else f"{_fmt(this.load)} {units} x {done}" if this.load else f"reps {done}"]
            if this.avg_rpe is not None:
                parts.append(f"avg RPE {_fmt(round(this.avg_rpe, 1))}"
                             + (f" (target {_fmt(target.target_rpe)})" if target else ""))
            if this.pr:
                parts.append("PR (reps or load)")
            e1rm = {p.week: p.e1rm for p in lift.e1rm_history}
            if lift.kind == "main" and week in e1rm:
                prev = max((w for w in e1rm if w < week), default=None)
                change = "" if prev is None else f" ({round(e1rm[week] - e1rm[prev]):+d} from week {prev})"
                parts.append(f"estimated max {round(e1rm[week])}{change}")
            if slot.plateau:
                parts.append("plateau")
            if slot.regressing:
                parts.append("regressing")
            parts.append(f"next step: {slot.next_step_reason}")
            lines.append(f"- {lift.exercise_id} ({lift.name}), day {slot.day}: " + "; ".join(parts))
            subjects[lift.exercise_id] = lift.name
    lines += ["</lifts>", f"<volume week {week}>"]
    for v in sorted((v for v in metrics.muscle_volume if v.week == week), key=lambda v: v.muscle):
        # Mid-week every muscle is "below" its weekly target, which isn't news.
        target = ("no target" if v.target_low is None
                  else f"target {_fmt(v.target_low)}-{_fmt(v.target_max)} for the full week" if in_progress
                  else f"target {_fmt(v.target_low)}-{_fmt(v.target_max)}, {v.status.replace('_', ' ')}")
        lines.append(f"- {v.muscle}: {_fmt(v.hard_sets)} hard sets{' so far' if in_progress else ''} ({target})")
        subjects[v.muscle] = v.muscle.replace("_", " ").capitalize()
    lines.append("</volume>")
    d = metrics.deload
    lines.append("deload: " + ("recommended: " + "; ".join(d.reasons) if d.recommended and d.reasons
                               else "recommended" if d.recommended else "not needed yet")
                 + (f"; next deload week {d.next_week}" if d.next_week else ""))
    text = "\n".join(lines)
    return Facts(week=week, in_progress=in_progress, text=text, numbers={_value(n) for n in NUMBER.findall(text)},
                 subjects=subjects, changes="\n".join(f"- {_clean(c)}" for c in changes[:MAX_CHANGES]))


# ---------- The grounding check ----------

def check_notes(notes: CoachNotes, facts: Facts) -> list[str]:
    """Problems to send back to the model; empty = the note is grounded and within limits."""
    problems: list[str] = []
    if not notes.headline.strip() or len(notes.headline) > MAX_HEADLINE:
        problems.append(f"headline must be 1-{MAX_HEADLINE} characters.")
    if not notes.focus.strip() or len(notes.focus) > MAX_FOCUS:
        problems.append(f"focus must be 1-{MAX_FOCUS} characters.")
    if not 1 <= len(notes.points) <= MAX_POINTS:
        problems.append(f"write 1 to at most {MAX_POINTS} points.")
    for p in notes.points:
        if p.subject not in facts.subjects:
            problems.append(f"subject {p.subject[:40]!r} isn't a lift or muscle in the facts; use one of their "
                            "ids or 'overall'.")
        if not p.text.strip() or len(p.text) > MAX_POINT:
            problems.append(f"each point must be 1-{MAX_POINT} characters.")
    texts = [notes.headline, notes.focus, *(p.text for p in notes.points)]
    invented = sorted({n for t in texts for n in NUMBER.findall(t) if _value(n) not in facts.numbers},
                      key=_value)
    if invented:
        problems.append(f"these numbers aren't in the facts: {', '.join(invented)}. Use only numbers from "
                        "<facts>, and write other amounts in words.")
    return problems


# ---------- Run ----------

def no_note(reason: str, *, retryable: bool, facts: Facts | None = None, attempts: int = 0) -> CoachNotesResponse:
    return CoachNotesResponse(source="none", week=facts.week if facts else None,
                              in_progress=facts.in_progress if facts else False, headline="", points=[],
                              focus="", reason=reason, retryable=retryable, attempts=attempts)


def run_notes(profile: Profile, program: Program, logs: list[SetLog], changes: list[str],
              llm: LLMClient | None, deadline_s: float = NOTES_DEADLINE_S,
              clock: Callable[[], float] = time.monotonic, week: int | None = None) -> CoachNotesResponse:
    from app.prompts import coach_notes as prompt

    facts = build_facts(profile, program, logs, changes, week)
    if facts is None:
        return no_note("Log a week of training and your coach notes will appear here.", retryable=False)
    if llm is None:
        return no_note("The AI coach isn't set up, so there are no notes.", retryable=False, facts=facts)
    start = clock()

    def generate(feedback: list[str]) -> CoachNotes:
        left = deadline_s - (clock() - start)
        if left < MIN_CALL_S:
            raise LLMUnavailable(f"notes time budget used up ({deadline_s:g} s)")
        return llm.generate(system=prompt.SYSTEM_PROMPT, user=prompt.user_message(profile, facts, feedback),
                            schema=CoachNotes, timeout_s=left)

    def check(notes: CoachNotes) -> Checked[CoachNotes | None]:
        return Checked(notes, [], check_notes(notes, facts))

    refined = refine(generate, check, lambda: None)
    attempts = len(refined.attempts)
    if refined.used_fallback or refined.value is None:
        log.warning("Coach notes failed (%s, prompt %s): %s; attempts: %s; error: %s", llm.name,
                    prompt.PROMPT_VERSION, refined.fallback_reason, refined.attempts, refined.error)
        reason = ("The AI coach is unavailable right now. Try again in a minute." if refined.error
                  else "The AI coach couldn't write notes that match your numbers this time. Try again.")
        return no_note(reason, retryable=True, facts=facts, attempts=attempts)
    notes = refined.value
    return CoachNotesResponse(
        source="ai", week=facts.week, in_progress=facts.in_progress, headline=notes.headline.strip(),
        points=[NotePointOut(subject=p.subject, label=facts.subjects.get(p.subject) or exercise_name(p.subject),
                             text=p.text.strip()) for p in notes.points],
        focus=notes.focus.strip(), reason=None, retryable=False, attempts=attempts)
