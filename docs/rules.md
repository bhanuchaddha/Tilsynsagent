# The rule set

What counts as a significant change, written in plain language before the agent
was built. The ordering matters: a rule set written after a system exists tends
to encode what the system already does rather than what it should do.

Version 1, 2026-08-07. Derived from 34 hand-labelled real changes drawn from
Plandata.dk history. Danish summary at the end.

---

## What is being watched

Municipal local plans (*lokalplaner*) at sub-area (*delområde*) level, across
Zealand. For each sub-area the register records the development rules that apply
to it. Five fields are watched:

| Field | Meaning |
|---|---|
| `maxbygnhjd` | maximum building height, metres |
| `maxetager` | maximum storeys |
| `bebygpct` | maximum built percentage of the plot |
| `zonestatus` | zone status (urban, rural, summer-house) |
| `anvendelsegenerel` | general permitted use |

A change is a transition of one sub-area between two versions where at least one
of those fields differs.

## The three outcomes

- **File** — this changes what may be built. Record it with a structured summary
  and a citation to the source document.
- **Escalate** — something changed, but the register alone cannot establish
  whether it matters. Hand it to a person with the before and after values and a
  statement of what is unclear.
- **Ignore** — nothing about what may be built has changed.

The third outcome carries as much weight as the first. A rule set that only
describes what to act on cannot distinguish "not covered" from "covered and
irrelevant", and a system that cannot tell those apart will escalate everything
or nothing.

---

## The rules

### R1 — A change in permitted use is always filed

If `anvendelsegenerel` changes from one value to another, file it.

Use decides what may be built at all, which outranks every dimensional limit. A
sub-area moving from *Boligområde* (residential) to *Område til offentlige
formål* (public purposes) changes the fundamental question of whether a
residential scheme is possible there.

This rule is deliberately unconditional. Distinguishing consequential use
changes from reclassification tidying would require knowing what the reader
intends to build, which the register does not contain.

### R2 — A change in a dimensional limit is filed

If `maxbygnhjd`, `maxetager`, or `bebygpct` changes from one value to another
value, file it, stating the direction.

Two shapes occur, and both are filed:

- **Proposal to adoption.** The plan was adopted with different numbers than
  were consulted on. Anyone who relied on the proposal is now working from the
  wrong figure.
- **Between two adopted versions.** What is currently permitted has changed.

### R3 — A field gaining a value for the first time is ignored

If a field was blank and now carries a value, and no other field changed value,
ignore it.

Nothing was loosened or tightened. The register was completed. This is the most
common single pattern in the data and the most common source of false alarms in
any naive change detector.

### R4 — A limit disappearing from the register is escalated

If a field carried a value and is now blank, escalate it, stating what the
value was.

The plan document behind the register has probably not changed. But the figure a
reader would quote is no longer recorded, and readers rely on the register. They
need to know it is gone, even though the cause is unclear.

**This rule is expensive and was chosen deliberately.** It is the single largest
source of escalations — roughly 45% of all observed transitions. The alternative
considered was to file these instead, since the agent can describe exactly what
happened ("the height limit is no longer stated; it previously read 8.5 m")
without needing human judgement. That would cut escalation volume by more than
half.

It was kept as escalate because a limit vanishing without explanation is
precisely the case where a reader would want to be asked rather than told.
Revisit this rule once there is evidence about whether the escalations are
actually useful to the person receiving them.

### R5 — Physically impossible records are escalated

If any version records more storeys than metres of height, escalate it.

A building cannot have more storeys than it has metres. The record is
unreliable, so any conclusion drawn from it is unreliable too.

Two sub-cases:

- **Transposed values.** Height and storeys are swapped between versions — 2 m
  and 8.5 storeys becomes 8.5 m and 2 storeys. Read literally this is a large
  height increase. Read correctly, a typo was fixed and nothing changed. The
  register cannot settle which.
- **Zero height.** A height limit of 0 m is not a restriction, it is a missing
  value written as a number.

### R6 — Built percentage falling to zero is escalated

If `bebygpct` changes from a value to 0, escalate it.

Taken literally, zero built percentage forbids all building — a severe
restriction. It is more likely a data entry than a decision, but the difference
matters enough that a person should confirm it against the plan document.

### R7 — Mixed additions and removals are escalated

If some fields gained values while others lost them in the same revision,
escalate it.

That mixture is more consistent with a record being reworked than with rules
changing, but it cannot be assumed.

---

## Rule precedence

Rules are evaluated in this order, first match wins:

1. R5 — physically impossible records
2. R6 — built percentage to zero
3. R1, R2 — real value changes (file)
4. R3 — pure additions (ignore)
5. R4 — pure removals (escalate)
6. R7 — mixed (escalate)

Data-integrity rules come first. A change derived from an unreliable record
should not be filed as though it were fact, whatever else it looks like.

## What this rule set does not cover

Stated explicitly, because the agent must escalate rather than guess when it
meets one of these:

- Sub-areas renumbered or split between versions — is this the same area under a
  new label, or a new area?
- A plan cancelled without an obvious replacement
- Changes to fields outside the five watched
- Anything requiring the source PDF to be read; the agent works from the
  register and cites the document, it does not interpret it

## Expected behaviour at this version

Applied to 1,148 real transitions from Zealand history:

| Outcome | Count | Share |
|---|---|---|
| File | 272 | 24% |
| Escalate | 631 | 55% |
| Ignore | 245 | 21% |

The escalation rate is high, and R4 is most of it. This is a known property of
version 1, not an accident. The number to watch over time is not the escalation
rate itself but whether the escalations turn out to be worth a person's
attention — and that evidence does not exist yet.

---

## Dansk sammenfatning

Systemet overvåger lokalplaner på delområdeniveau og reagerer på ændringer i
fem felter: maksimal bygningshøjde, maksimalt etageantal, bebyggelsesprocent,
zonestatus og generel anvendelse.

Tre udfald:

- **Arkivér** — ændringen påvirker, hvad der må bygges. Registreres med
  henvisning til kildedokumentet.
- **Eskalér** — noget er ændret, men registret alene kan ikke afgøre, om det har
  betydning. Sendes til et menneske med før- og efterværdier.
- **Ignorér** — intet er ændret i, hvad der må bygges.

Reglerne i korthed:

1. Ændret anvendelse arkiveres altid — anvendelsen afgør, hvad der overhovedet
   må bygges.
2. Ændrede mål (højde, etager, bebyggelsesprocent) arkiveres med angivelse af
   retning.
3. Et felt, der udfyldes for første gang, ignoreres — registret er blevet
   komplet, reglerne er ikke ændret.
4. En grænse, der forsvinder fra registret, eskaleres — tallet, læseren ville
   citere, findes ikke længere.
5. Fysisk umulige registreringer eskaleres — flere etager end meter i højden,
   eller en højde på 0 m.
6. Bebyggelsesprocent, der falder til 0, eskaleres.
7. Blandede tilføjelser og sletninger i samme revision eskaleres.

Regel 4 er den dyreste og står for hovedparten af eskaleringerne. Den er valgt
bevidst og bør revurderes, når der foreligger erfaring med, om eskaleringerne
rent faktisk er nyttige for modtageren.
