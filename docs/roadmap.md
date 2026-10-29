# Roadmap

What the app would still need to be a coach rather than a very good daily adviser, in the order
of what each item does for a runner who uses it every day - not in the order of effort. Written
2026-10-28 from a review of v0.2.2 against the code; each gap names where it lives.

The first item has since shipped (v0.3, "what comes next": the planner reads the history for a
brake, a down week, a measurement, a return week and the next stimulus, in code). It stays here
because the reasoning behind the order is the point of this file.

| # | Gap | For the runner | Effort |
|---|---|---|---|
| 1 | Progression without a plan: a brake, a down week when due, a measurement when due, the next stimulus, a return week - *shipped in v0.3* | very high | medium |
| 2 | A way back from the athlete: ill, sore, stressed, how it felt | high | medium |
| 3 | A session with your own structure, in your own words | high | medium |
| 4 | Rep targets by pace, not heart rate, once the pace is real | high (anyone without a chest strap) | small |
| 5 | The proposed session matched against the run that followed | high | medium |
| 6 | `runcoach today` for the morning; a sync when the page comes back into focus | medium | tiny |
| 7 | Deeper history on first install | medium | medium |
| 8 | Body weight in the sync | medium | small |
| 9 | Garmin's own readiness beside the app's | medium (trust) | small |
| 10 | Race preparation: base, build and taper towards a date | medium (those with a date) | high |
| 11 | Session sizes from the athlete's own time per weekday and usual run lengths | medium | small |

## 2. A way back from the athlete

"Heavy legs", "a cold", "a rough week at work", "that was on a treadmill", a perceived effort:
none of it can be recorded. What is said in chat is gone with the chat; a card remembers only
the previous card of its kind and a thumb. Illness is the most common reason a plan has to
change, and the watch sees it only once HRV reacts. The coach's own instructions ask about
"falling weight" with nowhere for the athlete to say so.

What it takes: a `check_ins` table (day, effort, flags, note), two taps on the Today tab (effort
plus chips for ill / sore / stressed), an MCP tool that unattended card runs cannot call, and
`ill` / `pain` as REST signals in `logic.readiness_verdict` - in code, not in the prompt.

## 3. A session with your own structure

"I have a 12 km loop, I want intervals on it" works today, but only as one of five kinds plus
a distance. The builder's rep menu is fixed (`planning.REP_MENU`), so "6×3", "10×400 m",
"8×1′/1′" cannot be asked for, a change request in a follow-up ("make it 6×3") runs into a tool
that cannot take it, there is no fartlek, no hills, no progression run, no tempo inside the long
run, distance and duration cannot both be given, and the app's form knows neither a day nor a
name although the tool does.

What it takes: a `structure` parameter the builder validates instead of chooses (rep length,
fits the route or the budget, spacing - the preview stays the gate), two or three more kinds,
and a free-text field beside the form whose words the agent turns into the structured call.

## 4. Rep targets by pace

VO2max and threshold reps go to the watch as heart-rate zones (`planning.py`, `hr_zone`). Heart
rate lags by 60–90 s, so on a 3–4 minute rep the watch spends a third of it saying "too low".
The usual practice is pace for reps and heart rate for easy running; the pace is already
computed. Since v0.3 the pace has a real source without a chest strap (Garmin's own 5 km and
10 km predictions), so this is now a method decision, and a small one.

## 5. The proposed session matched against the run

`runcoach_workouts` knows what was uploaded, `activities` knows what was run; nothing links
them (Garmin's activity list carries no workout reference). "Analyze this run" does not know
the run was Tuesday's 4×4, nor whether the reps reached the zone-5 floor. Matching by day and
sport, flagged as a match, would let the week strip say "as planned / deviated / not run" and
let the coach learn from its own prescriptions.

## 6. The morning

The product is needed at 07:00. There is no `runcoach today` printing the readiness text (a
cron plus any notifier would do the rest), and the web app syncs only at start and on ↻, so a
page left open overnight shows yesterday until someone clicks.

## 7. Deeper history on first install

The sync caps at 90 days, the default is 30–35, the Trend tab draws 56. On install day "why is
my VO2max not moving?" cannot be answered - the card says so itself. A one-off, throttled deep
backfill (daily metrics and the activity list, no detail) would give the founding question its
data.

## 8. Body weight

VO2max is ml/kg/min: two kilograms move it by about a point with no change in fitness. Garmin's
weigh-ins are not fetched, so the one confounder every athlete can name is not in the picture.

## 9. Garmin's own readiness

Every Garmin user will ask "Garmin says 85, you say EASY - why?", and the app cannot answer
because it never fetches the number. Showing both would cost two columns and buy trust.

## 10. Race preparation

A base → build → taper block towards a date, with the long run growing and the last two weeks
cutting volume while keeping intensity. It needs a race date and block state, and it is for the
runner with a race; for everyone else v0.3's rolling rules do what a block would have done
without the calendar - which is why this sits last, not first.

## Smaller

- Cards are written in English whatever the athlete's language; a `RUNCOACH_LANG` would be cheap.
- Strength and cycling count as hard days (good) but have no place in a planned week.
- Menstrual-cycle data, which Garmin has, appears nowhere.
- Every proposal creates a new Garmin workout and nothing deletes one; after months the library
  is full of "VO2max 4x4 min".
- No return-to-run after a symptom - deliberate, but the athlete is on their own afterwards.
- The evals run on a different model and in `--safe-mode`; a green suite is not the shipped coach.

## 11. Session sizes from the athlete's own time

v0.4 reads the athlete's days from their history - how many, which weekdays,
the long-run day, a weekly other sport (`habits.py`). It does not yet read how
long they have on each of those days: a runner whose Tuesday runs never go past
40 minutes can still be planned 55 there, and the easy runs and the long run
keep the proportions of the built-in week, scaled to the budget. The data is
there (durations per weekday); the reason it waited is that it would be a
second sizing rule beside `_week_sizes` and `_session_cap`, and every rendered
eval fixture would move with it. Done right it replaces the built-in
proportions with the athlete's own, under the same budget and the same cap.

