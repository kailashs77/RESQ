# What Was Built In This Session — Plain English Guide

This file explains, in simple language, what got added to the actual RESQ
project code in this session. Unlike the earlier research files (which were
just study notes), **this time real code was written and it runs.**

---

## The one-sentence version

We built "the brain" — the part of RESQ that takes traffic predictions and
actually decides which road the ambulance should take and when a traffic
signal should turn green — and connected it to a new tab on the website so
you can watch it make decisions live.

---

## Why this was needed

Before this session, the project had two separate pieces that didn't talk to
each other yet:

1. **Traffic forecasting models** (`forecast/models.py`) — two simple models
   that guess how fast traffic is moving on a road:
   - **Historical Average** — "what speed has this road usually had at this
     time of day/week, based on past data?"
   - **Spatial-Temporal** — "what speed is this road doing right now, and
     what are nearby roads doing, and is it trending up or down?"

2. **The live demo on the website** — which was actually just a scripted
   show. It didn't use the forecasting models at all; it used hand-typed
   numbers ("this road takes 32 seconds") and a simple on/off "accident"
   switch to fake an incident.

So the question was: **if you only had the forecasting models, could you
actually make a real decision from them?** This session builds that missing
middle piece.

---

## What "the brain" actually does, step by step

Think of it as an assembly line with four stations:

**Station 1 — Forecast.** Each of the two forecasting models looks at a
stretch of road and guesses a speed for it.

**Station 2 — Fuse.** If both models give an answer for the same road, we
don't just average them blindly. We use a statistics rule: **the model that
is more confident/certain gets more influence on the final number.** This is
the same "fusion" idea used elsewhere in the project for combining camera
data with traffic-feed data — we just reused and generalized it so it now
also works for combining forecasting models, and works with 2, 3, or 4
sources instead of being stuck at exactly 2.

**Station 3 — Decide the route.** Once every road segment has one trusted
travel-time number, we add those numbers up for each of the two possible
routes (the regular route and the ResQ bypass route) and compare them. If one
route is enough faster than the other — by more than 10 seconds AND more
than 5% — the brain switches routes. This "switch only if it's REALLY worth
it" rule already existed in the project (it stops the ambulance from
nervously flip-flopping between routes over tiny differences); we just
plugged real forecast numbers into it instead of a fixed script.

**Station 4 — Decide the traffic signals.** As the ambulance gets close to
each of three traffic signals along its route, the brain sends a "priority
request" (like the ambulance requesting an early green light) to the
signal's safety controller. That controller was already built and already
guarantees things like "never show green in two directions at once" — we
just made sure it now receives real requests generated from the real route
decision, instead of pre-scripted signal states.

---

## The important part: "what if a model fails or is turned off?"

This was the actual question you asked before this build, and the answer is
now proven, not just theoretical:

- **Both models working** → the brain blends them and decides.
- **Only one model working** (the other is switched off or crashes) → the
  brain just uses whichever one is left. It doesn't break.
- **Both models off/failed** → the brain falls back to the road's normal,
  everyday travel time (like a paper map would show) so it still gives an
  answer — just a less clever one.

We tested all three of these directly and confirmed the brain keeps working
in every case. This is the "even if one of the four fails, it should still
work" requirement from earlier in the conversation — done for the two models
that currently exist, and the fusion code is now written in a general way so
adding two more sources later (camera/perception, live traffic feed) won't
require rebuilding this part again.

---

## A nice side effect we noticed while building this

The Historical model and the Spatial-Temporal model don't always agree — and
that's actually useful, not a bug:

- The **Historical** model was trained ahead of time on "normal" traffic
  patterns, so if something unusual happens *right now* (like sudden
  congestion), it has no way to know about it — it keeps predicting normal
  speeds for a while.
- The **Spatial-Temporal** model looks at the last few seconds of data, so
  it notices sudden changes almost immediately.

So we added a small, honest rule: **when the two models disagree a lot, we
treat that disagreement itself as a warning sign and lean more on the
faster-reacting model.** This mirrors how real traffic systems are designed
— a slow, trained-in-advance model for the big picture, and a fast, reactive
model for "something just changed."

---

## What got added to the website

A new tab called **"Brain"** was added next to the existing "Live" tab. It
shows:

- The same live map as before, but now the route shown is picked by the real
  brain, not a script.
- Two on/off switches — one for each forecasting model — so you can turn
  them off one at a time and literally watch the decision change (or keep
  working) in real time.
- A switch to turn on a "congestion scenario," which fakes a sudden traffic
  jam on part of the normal route — this lets you *see* the brain reroute
  because of a forecast, without needing a fake "accident" button like the
  old demo used.
- A live decision log explaining, in plain sentences, what the brain just
  decided and why (e.g. "route-b selected from 2-model forecast fusion" or
  "Deciding on historical forecast alone").

---

## What is NOT done yet (be honest about this if asked)

- **Camera/perception input** is not connected to the brain yet — only
  forecasting is. The brain currently makes decisions blind to real
  blockages; it can only react to what the forecast predicts, not to a
  camera actually seeing a stopped car.
- **A live traffic feed / macro source** is also not connected yet, for the
  same reason — the fusion code is ready for it (it was generalized during
  this session specifically so this is easy to add later), but nothing
  currently sends it real data.
- The forecasting "history" the models learn from is still **synthetic
  (made-up but consistent) data**, not real recorded traffic — same honesty
  point as the earlier research files made about the rest of the project.
- We could not fully test the website changes in a real browser or run the
  live server in this session (the tools needed weren't installed in this
  environment), so the backend logic was tested directly and thoroughly, but
  someone should click through the actual "Brain" tab once to confirm it
  looks right.

---

## One paragraph you could say out loud about this session

*"This session connected the traffic forecasting models to an actual
decision-making brain for the first time. Before, forecasting existed but
nothing used its output. Now, the two forecasting models feed into a fusion
step that combines them by confidence, which feeds into the existing
route-switching logic and the existing traffic-signal safety controller, and
the whole thing is visible live on a new 'Brain' tab on the website. We
specifically tested and confirmed that the system keeps making sensible
decisions even if one forecasting model is turned off or fails, and falls
back to normal travel times if both are unavailable — which was the core
requirement going into this."*
