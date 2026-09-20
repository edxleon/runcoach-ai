# Training reference — zones, load, VO2max

Thresholds and reading rules for endurance running. General training theory, not medical advice. Percentages describe the model; the athlete's actual bounds come from their watch.

## Heart-rate zones (5-zone model, % of max HR)

| Zone | % max HR | Feels like | Purpose |
|---|---|---|---|
| Z1 recovery | 50–60 | very light, chatting | regeneration, warm-up |
| Z2 base | 60–70 | easy, nose breathing works | aerobic base — the bread and butter |
| Z3 tempo | 70–80 | "comfortably hard" | aerobic capacity, in moderation: the grey zone |
| Z4 threshold | 80–90 | hard, short sentences | lactate threshold, sustained speed |
| Z5 VO2max | 90–100 | near maximal | peak aerobic power, intervals |

Age formulas for max HR miss individuals by ± 10 bpm; a measured peak or the watch's bounds beat them. Zones anchored on lactate-threshold HR are more individual than %max.

**80/20:** ~80 % of training time easy (Z1–Z2), ~20 % hard (Z4–Z5). The classic recreational mistake is too much Z3 — always medium-hard: tiring without the quality of a real stimulus. Easy runs stay below ~75 % of max HR.

## ACWR — acute:chronic workload ratio

Load of the last 7 days against the weekly average of the last 28.

| ACWR | Reading | Consequence |
|---|---|---|
| < 0.8 | under-loaded | stimulus missing — volume may rise |
| 0.8–1.3 | sweet spot | sustainable, stay here |
| 1.3–1.5 | elevated | hold, do not keep ramping |
| > 1.5 | spike | clearly unload |

Limits: the bands come from team-sport studies, and the ratio's value as an injury predictor is disputed (the acute week is part of the chronic average). It flags load *spikes*; it does not predict injuries. A ratio computed from plain load sums is cruder than Garmin's weighted one: a holiday week deflates the chronic side and inflates the ratio; HR-based load under-counts strength and short anaerobic work, so it reads low right after them. Volume: the "10 % per week" rule is a convention, not a finding — what the data show is that jumps above ~30 % in two weeks go with more distance-related injuries, and that a graded 10 % programme did not reduce injuries in a trial.

## Training effect (Garmin, 0–5, aerobic and anaerobic per workout)

0–0.9 none · 1–1.9 minor (recovery) · 2–2.9 maintaining · 3–3.9 improving, a solid stimulus · 4–4.9 highly improving, not daily · 5.0 overreaching, rare and deliberate. Hard session ≈ aerobic TE ≥ 3.0 or anaerobic TE ≥ 2.0. After ≥ 4.0, at least one easy day.

## Garmin training status

| Status | Meaning | Tendency |
|---|---|---|
| PRODUCTIVE | load and fitness rising | carry on |
| MAINTAINING | fitness held | raise the stimulus to build |
| PEAKING | short-lived top form | race now |
| RECOVERY | deliberate unloading | keep it light |
| UNPRODUCTIVE | load high, fitness falling | often a sleep or recovery deficit |
| OVERREACHING | acute load too high | unload |
| DETRAINING | too little stimulus | add volume or intensity |
| STRAINED | stress exceeds adaptation | back off |

## The readiness light (this app's rule set, conservative)

| Signal | REST | EASY |
|---|---|---|
| HRV status | LOW / POOR | UNBALANCED |
| Sleep score | < 50 | 50–64 |
| Body Battery (day's peak) | < 40 | 40–59 |
| Resting HR vs 27-day baseline | ≥ +7 bpm | +4 to +6 |
| ACWR (Garmin) | > 1.5 | 1.3–1.5, or < 0.8 with > 2 days since a hard session |
| ACWR (computed) | never | > 1.3 |

One REST signal → REST; else one EASY signal → EASY; else GO. A GO resting on fewer than two recovery signals is downgraded to EASY (thin data). The light measures systemic recovery — not muscles, tendons or energy intake.

Long run: once a week, Z2, lengthened gradually. Quality: 1–2 per week, hard stimuli 48 hours apart.

## Raising VO2max

VO2max responds to **polarisation**: a large easy base *plus* true high-intensity work. The middle (Z3) does least for it.

| Lever | Concretely |
|---|---|
| Time near VO2max | the stimulus is **minutes at 90–95 % of max HR**. A few seconds of Z5 per hard session is almost no stimulus. |
| Rep length | **3–5 min** (5×3, 4×4). Reps of 30–60 s end before HR gets there: speed work, not VO2max. Reps beyond ~6 min drift into threshold work. |
| Recoveries | jog until HR really drops (Z1–Z2). Recovery HR within ~12 bpm of work HR turns the session into one continuous Z4 block. |
| Easy means easy | easy runs drifting into Z3 undermine recovery *and* base. |
| Patience | response takes 6–8 weeks of consistent work; Garmin's estimate wobbles by ± 0.5–1. Judge over 4–6 weeks. |

Plateau patterns: much Z3 with little Z1–2 → unpolarised: more true easy running plus targeted Z5 work. Hard sessions with hardly any Z5 time → lengthen the reps.

Garmin's estimate is anchored on HR versus pace. Heat, hills, trails and a run without ~10 steady minutes push it down without any fitness change; a higher max-HR setting lifts it slightly.

## Lactate threshold

The effort sustainable for roughly an hour; its heart rate (LTHR) usually sits at 85–92 % of max. Garmin re-measures it on hard runs and stamps the date — an old measurement is an old number. Threshold sessions: 3–4 × 8–10 min or 20–40 min continuous in low Z4, recoveries short. They count as hard for spacing, raise sustainable pace, and are *not* a VO2max stimulus.

## What Garmin's settings drive

- **Max-HR auto-detection only raises** the value. Garmin never lowers max HR by itself; zones shift because an anchor moved up (new HR peak, new LTHR) or the basis was changed by hand (%max ↔ %LTHR ↔ custom).
- **Load focus** (low aerobic / high aerobic / anaerobic) is EPOC-based and independent of the user's zone settings.
- User zones drive zone colours, time-in-zone statistics and intensity minutes — not VO2max, load focus or training effect.

## Periodisation

- **Base:** much Z2 volume, little intensity.
- **Build:** more Z4/Z5 quality while volume holds.
- **Peak / taper:** volume down 40–60 % over the last ~2 weeks, intensity and frequency kept — the meta-analytic optimum; cutting intensity too is the classic taper mistake.
- **Down week:** every 3–4 weeks, load −30–40 %, so adaptation can land. A low ACWR in that week is the plan working.

## Recovery

- Sleep is the strongest lever; a deficit shows up in HRV, readiness and status within days.
- Resting HR up *and* HRV down over several days is a warning pattern (oncoming illness, overload, life stress): take volume and intensity out.

## Sources — and how much weight each claim carries

Three grades. **Evidence**: a trial or meta-analysis says so. **Convention**: what coaches do and the literature does not contradict; no direct test. **This app**: a threshold chosen here, conservative on purpose, stated so it can be argued with.

| Claim | Grade | Source |
|---|---|---|
| ~80 % easy / ~20 % hard; the middle does least | Evidence (elite observational + one RCT) | Seiler & Kjerland 2006, Scand J Med Sci Sports 16:49, doi:10.1111/j.1600-0838.2004.00418.x · Stöggl & Sperlich 2014, Front Physiol 5:33, doi:10.3389/fphys.2014.00033 |
| VO2max: minutes at 90–95 % max HR, reps of 3–5 min | Evidence | Helgerud et al. 2007, Med Sci Sports Exerc 39:665, doi:10.1249/mss.0b013e3180304570 · Buchheit & Laursen 2013, Sports Med 43:313, doi:10.1007/s40279-013-0029-x |
| ACWR sweet spot 0.8–1.3, spike > 1.5 | Evidence, team sports; **disputed** | Gabbett 2016, Br J Sports Med 50:273, doi:10.1136/bjsports-2015-095788 · critique: Impellizzeri et al. 2020, Int J Sports Physiol Perform 15:907, doi:10.1123/ijspp.2019-0864 |
| Weekly volume progression | Evidence, weak | Nielsen et al. 2014, J Orthop Sports Phys Ther 44:739, doi:10.2519/jospt.2014.5164 (> 30 % in two weeks; HR 1.59, CI crosses 1) · Buist et al. 2008, Am J Sports Med 36:33, doi:10.1177/0363546507307505 (10 % programme, no effect) |
| Age formulas miss by ± 10 bpm | Evidence | Tanaka, Monahan & Seals 2001, J Am Coll Cardiol 37:153, doi:10.1016/S0735-1097(00)01054-8 (208 − 0.7 × age; SD ≈ 10) |
| Taper: −40–60 % volume over ~2 weeks, intensity kept | Evidence (meta-analysis) | Bosquet et al. 2007, Med Sci Sports Exerc 39:1358, doi:10.1249/mss.0b013e31806010e0 |
| Resting HR and HRV as recovery signals | Evidence for the signals; the thresholds are **this app** | Buchheit 2014, Front Physiol 5:73, doi:10.3389/fphys.2014.00073 — the +4/+7 bpm and sleep/Body Battery cut-offs in the readiness table have no trial behind them |
| Training Effect scale, training status labels | Vendor definition | Firstbeat, "EPOC Based Training Effect Assessment" (white paper) and Garmin's Training Status documentation |
| Low energy availability: warning signs, see a professional | Evidence (consensus) | Mountjoy et al. 2023, Br J Sports Med 57:1073, doi:10.1136/bjsports-2023-106994 |
| Hard sessions 48 h apart; long run weekly; down week every 3–4 weeks | Convention | Standard periodisation practice; consistent with the polarised model above, not separately tested |
| LTHR ≈ 85–92 % of max HR; easy cap ≈ 0.83 × Z5 bound | Convention | Coaching heuristics; the athlete's measured LTHR and zone bounds replace them whenever present |
