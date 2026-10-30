# Roadmap

What the app would still need to be a coach rather than a very good daily adviser, in the order
of what each item does for a runner who uses it every day - not in the order of effort. Written
2026-10-28 from a review of v0.2.2 against the code; each gap names where it lives. Reordered
2026-10-30 after a second review against daily use, which added three gaps (3, 8, 10), widened
two (2, 14) and ordered the rest by what each does for the runner.

The first item has since shipped (v0.3, "what comes next": the planner reads the history for a
brake, a down week, a measurement, a return week and the next stimulus, in code). It stays here
because the reasoning behind the order is the point of this file.

| # | Gap | For the runner | Effort |
|---|---|---|---|
| 1 | Progression without a plan: a brake, a down week when due, a measurement when due, the next stimulus, a return week - *shipped in v0.3* | very high | medium |
| 2 | A way back from the athlete: ill, sore, stressed, how it felt - with a state that lasts until it is over | high | medium |
| 3 | An easier option for every hard session in the plan, computed by the rules | high | small |
| 4 | Rep targets by pace, not heart rate, once the pace is real | high (anyone without a chest strap) | small |
| 5 | A session with your own structure, in your own words | high | medium |
| 6 | The proposed session matched against the run that followed | high | medium |
| 7 | Session sizes from the athlete's own time per weekday and usual run lengths | medium to high (every planned week) | small |
| 8 | Monotony and weekly strain as a second load signal beside ACWR | medium (rarely fires) | small |
| 9 | `runspector today` for the morning; a sync when the page comes back into focus | medium | tiny |
| 10 | Where each statement on a card comes from: measured, said by you, or derived | medium (trust) | medium |
| 11 | Garmin's own readiness beside the app's | medium (trust) | small |
| 12 | Body weight in the sync | medium | small |
| 13 | Deeper history on first install | medium (install day only) | medium |
| 14 | Race preparation towards a goal race: base, build and taper, with the race itself known | medium (those with a date) | high |

Why this order: the easier option (3) helps on every hard day and costs almost nothing, because
the rules already know the easy version of each session; it comes before pace targets (4) because
it needs neither a chest strap nor a new measurement. Session sizes from the athlete's own time
(7) shape every planned week, so they come before monotony (8), which for most hobby runners
rarely fires. Of the trust items, where a statement comes from (10) touches every card and so
comes before Garmin's own number (11). Deeper history (13) helps on install day only.

## 2. A way back from the athlete

"Heavy legs", "a cold", "a rough week at work", "that was on a treadmill", a perceived effort:
none of it can be recorded. What is said in chat is gone with the chat; a card remembers only
the previous card of its kind and a thumb. Illness is the most common reason a plan has to
change, and the watch sees it only once HRV reacts. The coach's own instructions ask about
"falling weight" with nowhere for the athlete to say so. The You tab's note (v0.4.1) is a first,
free-text half of this: the coach reads it, no rule acts on it.

A check-in is not a single day. A cold has a start, days it is still there and an end; a sore
calf comes back two days later. So a check-in carries a state: since when, when it was last
said, and whether it is over - ended by the athlete ("feeling fine again") or by running out
after a set number of days without a new word. While `ill` or `pain` is open, the day is REST,
whatever HRV says; the morning after it ends is easy, not hard.

What it takes: a `check_ins` table (day, effort, flags, note, since, last said, over), two taps
on the Today tab (effort plus chips for ill / sore / stressed, and "over" on an open one), an MCP
tool that unattended card runs cannot call, and the open state as its own step in
`logic.decide_today` - in code, not in the prompt, and not in `readiness_verdict`, where a
check-in made before the morning sync would be lost.

## 3. An easier option for every hard session

The plan says "Thursday: 4×4 min VO2max". On Thursday the legs are heavy, the readiness is
yellow, or there are only 30 minutes - and the plan has nothing to say, so the session is either
forced or dropped. A coach would have said it in advance: "if you're tired, 30 minutes easy
instead", "if short on time, the long run at 60 % of its length".

What it takes: for every hard session and every long run, the alternative the rules would
choose on an EASY day (`logic` already knows it: the same day with the hard parts removed, or
the long run cut to the easy cap), shown as one line under the session in the week preview
(`planning.build_week`) and sent as the workout's description to the watch (`plan.py`; the
upload sets none today), so it is there at the start of the run. A scenario test: every hard
session has an alternative, and the alternative is never harder than the session.

## 4. Rep targets by pace

VO2max and threshold reps go to the watch as heart-rate zones (`planning.py`, `hr_zone`). Heart
rate lags by 60–90 s, so on a 3–4 minute rep the watch spends a third of it saying "too low".
The usual practice is pace for reps and heart rate for easy running; the pace is already
computed. Since v0.3 the pace has a real source without a chest strap (Garmin's own 5 km and
10 km predictions), so this is now a method decision, and a small one.

## 5. A session with your own structure

"I have a 12 km loop, I want intervals on it" works today, but only as one of five kinds plus
a distance. The builder's rep menu is fixed (`planning.REP_MENU`), so "6×3", "10×400 m",
"8×1′/1′" cannot be asked for, a change request in a follow-up ("make it 6×3") runs into a tool
that cannot take it, there is no fartlek, no hills, no progression run, no tempo inside the long
run, distance and duration cannot both be given, and the app's form knows neither a day nor a
name although the tool does.

What it takes: a `structure` parameter the builder validates instead of chooses (rep length,
fits the route or the budget, spacing - the preview stays the gate), two or three more kinds,
and a free-text field beside the form whose words the agent turns into the structured call.

## 6. The proposed session matched against the run

`runcoach_workouts` knows what was uploaded, `activities` knows what was run; nothing links
them (Garmin's activity list carries no workout reference). "Analyze this run" does not know
the run was Tuesday's 4×4, nor whether the reps reached the zone-5 floor. Matching by day and
sport, flagged as a match, would let the week strip say "as planned / deviated / not run" and
let the coach learn from its own prescriptions.

## 7. Session sizes from the athlete's own time

v0.4 reads the athlete's days from their history - how many, which weekdays,
the long-run day, a weekly other sport (`habits.py`). It does not yet read how
long they have on each of those days: a runner whose Tuesday runs never go past
40 minutes can still be planned 55 there, and the easy runs and the long run
keep the proportions of the built-in week, scaled to the budget. The data is
there (durations per weekday); the reason it waited is that it would be a
second sizing rule beside `_week_sizes` and `_session_cap`, and every rendered
eval fixture would move with it. Done right it replaces the built-in
proportions with the athlete's own, under the same budget and the same cap.

## 8. Monotony and weekly strain

ACWR compares this week's load with the last four; it does not see how the load is spread. Seven
similar medium days and a week of three hard days and four rest days can carry the same total
and the same ACWR, but the first leaves no day to recover. Foster's monotony (the week's mean
daily load divided by its standard deviation) and strain (weekly load times monotony) name that:
high monotony together with high strain is the pattern linked to illness and staleness.

What it takes: a pure function in `logic.py` over the daily loads the store already has, a brake
in `planning.py` with its own assumption line ("your last week was very even and heavy - Sunday
easy, Wednesday off"), one row on the Trend tab. The thresholds come from the literature and are
checked before they are used; a scenario test red against the old code first. For most hobby
runners it will rarely fire - which is why it is item 8, not item 2.

## 9. The morning

The product is needed at 07:00. There is no `runspector today` printing the readiness text (a
cron plus any notifier would do the rest), and the web app syncs only at start and on ↻, so a
page left open overnight shows yesterday until someone clicks.

## 10. Where each statement on a card comes from

A card says "your HRV has been low for four days", "you said Tuesdays are short", "your long run
is too fast". The first is measured, the second is the athlete's own word, the third is the
coach's reading - and on the card they look the same. A reader cannot tell a number from an
opinion, and neither can the eval judge.

What it takes: the card frame asks for the source of every claim in three kinds - measured (the
watch, with its date), from you (the You tab, the note, a check-in), derived (a rule or the
coach's reading, with what it was derived from) - shown as a small mark beside the bullet
(`skills/coach.md`, the card frame in `web/agent.py`, `cards.js`). An eval case checks it: a
claim marked measured that no tool returned fails the card.

## 11. Garmin's own readiness

Every Garmin user will ask "Garmin says 85, you say EASY - why?", and the app cannot answer
because it never fetches the number. Showing both would cost two columns and buy trust.

## 12. Body weight

VO2max is ml/kg/min: two kilograms move it by about a point with no change in fitness. Garmin's
weigh-ins are not fetched, so the one confounder every athlete can name is not in the picture.

## 13. Deeper history on first install

The sync caps at 90 days, the default is 30–35, the Trend tab draws 56. On install day "why is
my VO2max not moving?" cannot be answered - the card says so itself. A one-off, throttled deep
backfill (daily metrics and the activity list, no detail) would give the founding question its
data.

## 14. Race preparation

A base → build → taper block towards a date, with the long run growing and the last two weeks
cutting volume while keeping intensity. It needs a race date and block state, and it is for the
runner with a race; for everyone else v0.3's rolling rules do what a block would have done
without the calendar - which is why this sits last, not first.

The race itself belongs on the You tab: a name, a date (also a rough one - "end of September"
is enough to plan the base), a priority (A: the race the block is built for; B and C: raced on
the way, with a short taper or none) and a target time, which sets the paces once it is closer
than the predictions. Where the race and the athlete's week disagree - the race is on a Sunday
and Sunday is a day off, or the target time needs more weekly running than the runs per week
allow - the app asks one question before it plans, and holds the plan until it is answered. The
block stays code; the question is the only place the conversation shapes it.

## Smaller

- Cards are written in English whatever the athlete's language; a `RUNSPECTOR_LANG` would be cheap.
- Strength and cycling count as hard days (good) but have no place in a planned week.
- Menstrual-cycle data, which Garmin has, appears nowhere.
- Every proposal creates a new Garmin workout and nothing deletes one; after months the library
  is full of "VO2max 4x4 min".
- No return-to-run after a symptom - deliberate, but the athlete is on their own afterwards.
- The evals run on a different model and in `--safe-mode`; a green suite is not the shipped coach.
- The simulator has no runner who reports pain the day after a quality session, none whose
  settings contradict themselves (five runs a week, three free days), and no proposal that goes
  stale before it is accepted.
