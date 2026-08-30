# The rule set

What counts as a significant change, written in plain language before the agent
was built. The ordering matters: a rule set written after a system exists tends
to encode what the system already does rather than what it should do.

Version 2, 2026-08-29. Derived from version 1 (2026-08-07, 34 hand-labelled
real changes drawn from Plandata.dk history) by asking a different question of
the same data. Danish summary at the end. Version 1's full text, reasoning, and
now-superseded statistics are preserved below under
[Version history](#version-history).

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

## The question this rule set answers

Version 1 was written from the **register's** point of view: did the governing
rules actually change? Under that question a blank field being filled in reads
as a data-completeness fix, not a rule change, so version 1 ignored it.

This rule set is for a reader who relies on the register, and the question that
matters to them is different: **do I now see something I did not see before, on
land I care about?** Under that question, the same event is material. A
building-percentage limit now existing where none was stated is exactly the
kind of thing someone watching that sub-area needs to be told about, whatever
the cause behind it.

That reframing is why version 1 was internally inconsistent about its own
premise: it escalated a value disappearing (a reader relies on the register, so
losing a figure is worth flagging) while ignoring a value appearing (only the
plan document counts). Two opposite premises, three rules apart. This version
answers one question throughout.

## The two outcomes

- **File** — the reader now sees something material: a limit changed, or a
  limit exists where none was recorded before. Record it with a structured
  summary and a citation to the source document.
- **Escalate** — something changed, but the register alone cannot establish
  what it means, or a limit the reader could previously see is now missing.
  Hand it to a person with the before and after values and a statement of what
  is unclear.

**`ignore` is not reachable from these rules, deliberately.** Concluding
"nothing changed" or "the register was completed, not the rules" is a claim
about the plan document behind the register — a document this rule layer never
opens. The project's one rule is that every autonomous decision must be
traceable to the source that justified it; an `ignore` produced by the register
alone would not be traceable to anything, it would be an assumption wearing the
shape of a decision. So this rule set only ever files or escalates. A genuine
"nothing here, and I checked" conclusion re-enters later, once something reads
the actual plan document and can cite the clause that makes it true — see
"What this rule set does not cover," below, and the project's later phases.

---

## The rules

Precedence, first match wins.

### R1 — Physically impossible records are escalated

If either version of the record is physically impossible, escalate it, stating
what makes it impossible.

Three sub-cases, all meaning the record cannot be relied upon:

- **Transposed values.** Height and storeys are swapped between versions — 2 m
  and 8.5 storeys becomes 8.5 m and 2 storeys. Read literally this is a large
  height increase. Read correctly, a typo was fixed and nothing changed. The
  register cannot settle which.
- **Zero height.** A height limit of 0 m is not a restriction, it is a missing
  value written as a number.
- **Built percentage falling to zero.** Taken literally, zero built percentage
  forbids all building — a severe restriction. It is more likely a data entry
  than a decision, but the difference matters enough that a person should
  confirm it against the plan document. (A field showing 0 for the first time,
  with nothing to fall from, is not this case — see R3.)

A conclusion drawn from an unreliable record is unreliable too, whatever else
it looks like. This is why R1 runs first: nothing downstream should be filed as
though it were fact when the record itself cannot be trusted.

### R2 — A watched field value changing to a different value is filed

If any of the five watched fields changes from one non-blank value to a
different non-blank value, file it.

This now includes zone status, which version 1 could not decide. A sub-area
moving from *Byzone* to *Landzone* is exactly as visible a change to the
reader as a height limit moving from 8.5 m to 10 m — the register said one
thing, now it says another. Closing this gap was the one open hole in version
1: a real zone-status change fell through every rule and reached "not
covered" every time, with no evidence about what the reader should be told.

When more than one field changes at once, the reason text states them in this
order of consequence — use, then dimensions, then zone — but the outcome is
the same regardless: file.

- **Use** decides what may be built at all, which outranks every dimensional
  limit. A sub-area moving from *Boligområde* (residential) to *Område til
  offentlige formål* (public purposes) changes the fundamental question of
  whether a residential scheme is possible there.
- **Dimensional limits** (height, storeys, built percentage) changing is filed
  stating the direction, whether the shift is proposal-to-adoption (the plan
  was adopted with different numbers than were consulted on) or between two
  adopted versions (what is currently permitted has changed).
- **Zone status** changing alone is filed on the same basis as the others: the
  reader sees a different zone status than they saw before.

### R3 — A field gaining a value is filed

If a field was blank and now carries a value, and no field lost a value in the
same revision, file it.

Version 1 ignored this on the theory that nothing was loosened or tightened —
the register was merely completed. That theory answers the register's
question, not the reader's. A reader watching this sub-area did not see a
built-percentage limit before; now they do. Whatever caused the register to
be completed, the reader's picture of what may be built on that land just
became more specific than it was, and that is exactly the kind of thing this
system exists to tell them about.

### R4 — A field losing its value is escalated

If a field carried a value and is now blank — alone, or alongside other fields
gaining values in the same revision — escalate it, stating what the value was.

The plan document behind the register has probably not changed. But the figure
a reader would quote is no longer recorded, and readers rely on the register.
They need to know it is gone, even though the cause is unclear.

This absorbs version 1's separate rule for mixed additions and removals: a
revision with both a gain and a loss is not a distinct case requiring its own
rule, it is simply R4 winning on precedence over R3 — a limit that disappeared
matters more than a limit that appeared, in the same revision, for the same
reason R1 runs before everything else: the reader needs to know what they can
no longer trust, first.

### No rule matches: not covered

If the sub-area has been seen before, a new version now exists, but no watched
field differs from the version last stored — none of R1 through R4 fire, and
the case is **not covered**. It is handed to the model's `assess()` step and
always escalates from there.

This is the honest version of "nothing changed here." A new version existing
at all means something moved in the underlying record; if it did not move in
one of the five watched fields, it moved somewhere this rule set does not
look, and the register alone cannot say where. Asserting "unchanged" from that
position would be exactly the untraceable decision the project's one rule
forbids. Escalating it instead means a person — or, in a later phase, a model
that has actually read the source PDF — settles what actually happened.

A sub-area's very first sighting is not this case. There is no previous
version to have differed from, so nothing has "changed" in any sense; this is
deduplication, handled silently before the rule engine runs at all, not a rule
outcome.

---

## Rule precedence

Rules are evaluated in this order, first match wins:

1. R1 — physically impossible records
2. R2 — real value changes (file)
3. R3 — pure additions (file)
4. R4 — any removal, alone or mixed with additions (escalate)
5. *(no rule matches)* — not covered, handed to `assess()`, always escalates

Data-integrity comes first, for the same reason it did in version 1: a
conclusion drawn from an unreliable record should not be filed as fact,
whatever else it looks like. R4 outranking R3 is the one precedence choice
that is not "most severe first" by coincidence — it is the whole content of
what used to be a separate rule for mixed changes.

## What this rule set does not cover

Stated explicitly, because the agent must escalate rather than guess when it
meets one of these:

- Sub-areas renumbered or split between versions — is this the same area under a
  new label, or a new area?
- A plan cancelled without an obvious replacement
- Changes to fields outside the five watched
- A revision where no watched field differs from the version last stored (see
  "No rule matches: not covered," above) — something moved, but not
  somewhere this rule set looks
- Anything requiring the source PDF to be read; the agent works from the
  register and cites the document, it does not interpret it. This is a
  version-2-scoped boundary, not a permanent one: a later phase that grounds
  a decision in the actual clause text of the source PDF is what brings a
  genuine `ignore` back into this system, honestly arrived at rather than
  assumed from the register.

## Expected behaviour at this version

**Pending recomputation.** Version 1's table (1,148 real transitions: 24% file,
55% escalate, 21% ignore) was produced by the Phase 0 analysis script run
against the full historical record under version 1's rules. That script is not
available in this working session, so this table cannot be honestly recomputed
against version 2 right now rather than edited by hand — carrying version 1's
numbers forward under version 2's rules would misstate what version 2 actually
does, since R3 alone reverses roughly a fifth of the population from ignore to
file.

What can be said without re-running that analysis: version 2 reaches only two
outcomes, so the 21% that was "ignore" under version 1 redistributes into
`file` (the R3 population, now filed) and the not-covered-then-escalated
population (the zone-status-alone transitions version 1 could not decide, now
resolved to `file` under R2). The escalation share should fall from roughly
55%, since R4 no longer absorbs the R3 population and zone status is no longer
a source of not-covered cases — but the exact number needs the same script
version 1's table came from, run again.

The golden dataset's own 34-case composition **is** recomputed, mechanically,
in [`evals/golden/README.md`](../evals/golden/README.md): 22 file, 12 escalate,
0 ignore.

---

## Dansk sammenfatning

Systemet overvåger lokalplaner på delområdeniveau og reagerer på ændringer i
fem felter: maksimal bygningshøjde, maksimalt etageantal, bebyggelsesprocent,
zonestatus og generel anvendelse.

Spørgsmålet, der besvares, er ikke længere "ændrede reglerne sig reelt?" men
"ser læseren nu noget, de ikke så før, på et areal de interesserer sig for?"
Under det spørgsmål er et felt, der udfyldes for første gang, lige så
væsentligt som et felt, der ændrer værdi.

To udfald:

- **Arkivér** — læseren ser nu noget væsentligt: en grænse ændrede sig, eller en
  grænse findes nu, hvor ingen var angivet før. Registreres med henvisning til
  kildedokumentet.
- **Eskalér** — noget er ændret, men registret alene kan ikke afgøre hvad det
  betyder, eller en grænse, læseren tidligere kunne se, er nu forsvundet.
  Sendes til et menneske med før- og efterværdier.

**"Ignorér" er bevidst ikke et muligt udfald af disse regler.** At konkludere
"intet ændrede sig" er en påstand om plandokumentet bag registret — et
dokument, som regellaget aldrig åbner. Et sådant "ignorér" ville ikke kunne
spores til noget som helst.

Reglerne i korthed:

1. Fysisk umulige registreringer eskaleres — flere etager end meter i højden,
   en højde på 0 m, eller bebyggelsesprocent, der falder til 0.
2. Et vagtfelt, der ændrer værdi til en anden værdi — inklusive zonestatus —
   arkiveres.
3. Et felt, der får en værdi for første gang, arkiveres — ikke ignoreres.
   Læseren ser nu en grænse, de ikke så før.
4. Et felt, der mister sin værdi — alene eller sammen med felter, der får en
   værdi i samme revision — eskaleres. Tallet, læseren ville citere, findes
   ikke længere.
5. Ingen regel passer: hvis delområdet er set før, en ny version findes, men
   intet overvåget felt er ændret, er sagen ikke dækket og sendes til
   vurdering — og eskaleres altid derfra.

---

## Version history

### Version 1, 2026-08-07

Superseded by version 2 above. Preserved here verbatim, including its
reasoning and its statistics, because the reversal from version 1 to version 2
is itself part of what this project demonstrates — see the root `CLAUDE.md`.
Nothing below this point is current.

---

Derived from 34 hand-labelled real changes drawn from Plandata.dk history.
Danish summary at the end.

### What is being watched

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

### The three outcomes

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

### The rules

#### R1 — A change in permitted use is always filed

If `anvendelsegenerel` changes from one value to another, file it.

Use decides what may be built at all, which outranks every dimensional limit. A
sub-area moving from *Boligområde* (residential) to *Område til offentlige
formål* (public purposes) changes the fundamental question of whether a
residential scheme is possible there.

This rule is deliberately unconditional. Distinguishing consequential use
changes from reclassification tidying would require knowing what the reader
intends to build, which the register does not contain.

#### R2 — A change in a dimensional limit is filed

If `maxbygnhjd`, `maxetager`, or `bebygpct` changes from one value to another
value, file it, stating the direction.

Two shapes occur, and both are filed:

- **Proposal to adoption.** The plan was adopted with different numbers than
  were consulted on. Anyone who relied on the proposal is now working from the
  wrong figure.
- **Between two adopted versions.** What is currently permitted has changed.

#### R3 — A field gaining a value for the first time is ignored

If a field was blank and now carries a value, and no other field changed value,
ignore it.

Nothing was loosened or tightened. The register was completed. This is the most
common single pattern in the data and the most common source of false alarms in
any naive change detector.

#### R4 — A limit disappearing from the register is escalated

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

#### R5 — Physically impossible records are escalated

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

#### R6 — Built percentage falling to zero is escalated

If `bebygpct` changes from a value to 0, escalate it.

Taken literally, zero built percentage forbids all building — a severe
restriction. It is more likely a data entry than a decision, but the difference
matters enough that a person should confirm it against the plan document.

#### R7 — Mixed additions and removals are escalated

If some fields gained values while others lost them in the same revision,
escalate it.

That mixture is more consistent with a record being reworked than with rules
changing, but it cannot be assumed.

### Rule precedence

Rules are evaluated in this order, first match wins:

1. R5 — physically impossible records
2. R6 — built percentage to zero
3. R1, R2 — real value changes (file)
4. R3 — pure additions (ignore)
5. R4 — pure removals (escalate)
6. R7 — mixed (escalate)

Data-integrity rules come first. A change derived from an unreliable record
should not be filed as though it were fact, whatever else it looks like.

### What this rule set does not cover

Stated explicitly, because the agent must escalate rather than guess when it
meets one of these:

- Sub-areas renumbered or split between versions — is this the same area under a
  new label, or a new area?
- A plan cancelled without an obvious replacement
- Changes to fields outside the five watched
- Anything requiring the source PDF to be read; the agent works from the
  register and cites the document, it does not interpret it

### Expected behaviour at this version

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

### Dansk sammenfatning (version 1)

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
