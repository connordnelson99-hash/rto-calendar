# Digest markdown — what changed, and what your skill needs to do differently

The calendar's **Copy/Download markdown** (the weekly digest export) changed shape
because the screener changed. This is the contract your newsletter skill reads.

## The one idea behind the change

The screener used to write a single summary that mixed two different things: what
the document actually said, and why it mattered to hydro. Nothing marked which was
which, so a downstream reader had no way to tell a quoted fact from an inference —
and would confidently republish invented mechanics. (Real example: an ISO-NE
accreditation deck that never says "hydro" produced "pumped storage uses full outage
data rather than the 5% assumed for batteries.")

Those are now **two separate fields with two separate headings.** Keep them separate.

## Per-document block: before → after

**Before**

```markdown
#### [HYDRO] Some Document Title
- **Type:** presentation · **Posted:** 2026-07-28
- **Why flagged:** <reason>
- **Stakeholders:** ...
- **URL:** ...

**AI summary:**

<one blended paragraph: what it says + why hydro cares>
```

**After**

```markdown
#### [HYDRO] Some Document Title
- **Type:** presentation · **Posted:** 2026-07-28
- **Confidence:** Direct — Changes a hydro or PSH revenue stream, obligation, or option
- **Topics:** Capacity & RA · Storage & PSH
- **Why flagged:** <reason>
- **Stakeholders:** ...
- **URL:** ...

**What the document says:**

<document-grounded only — will NOT name hydro if the document doesn't>

**Why it matters to hydro** — inference, not stated in the document:

<the hydro argument>

> Note: this document never uses the words hydro, pumped storage, or PSH.
> Do not attribute any hydro-specific rule to it.

**Sourced from the document** — verbatim spans behind the dates and figures above:

- Comments due August 13, 2026 — "Submissions are requested by close of business on August 13, 2026."
- [UNVERIFIED] Some figure — "quoted span that could not be matched"

> [UNVERIFIED] means the quote could not be matched against the extracted text.
> Open the URL and confirm before republishing that specific.
```

## Exact strings to key on

| Element | Literal string | Notes |
|---|---|---|
| Summary heading | `**What the document says:**` | **Replaces `**AI summary:**`** |
| Read-through heading | `**Why it matters to hydro** — inference, not stated in the document:` | New |
| Evidence heading | `**Sourced from the document** — verbatim spans behind the dates and figures above:` | New |
| Confidence line | `- **Confidence:** ` | New; `Direct` / `Precedent` / `Reference` |
| Topics line | `- **Topics:** ` | New; ` · `-separated |
| Unverified marker | `[UNVERIFIED] ` | Prefix on an evidence bullet |

Em dashes (`—`) are literal in all of these.

## Rules for the newsletter

1. **Attribute by section.** Content under *What the document says* can be stated as
   what the RTO said or proposed. Content under *Why it matters to hydro* must be
   voiced as implication — "this would determine…", "hydro owners should expect…".
   Never promote a read-through sentence into a claim about the document.

2. **Never invent per-technology mechanics.** No thresholds, exemptions, carve-outs,
   or battery-vs-PSH treatments unless they appear in a *Sourced from* quote. These
   are the characteristic failure — they sound plausible because they are the kind
   of thing such a document might contain.

3. **Honor the no-hydro note.** If a block carries the "never uses the words hydro"
   blockquote, no sentence you publish may attribute a hydro-specific rule to that
   document. Say what the general rule is, say hydro falls under it, stop.

4. **Check `[UNVERIFIED]` before republishing a date or figure.** Prefer quoting the
   verbatim span over the summary's paraphrase when precision matters — deadlines
   especially.

5. **Weight by Confidence.** Lead with `Direct`. Frame `Precedent` explicitly as a
   read-through or cross-market comparison, never as something that happened to
   hydro — all ERCOT items are `Precedent` at most. `Reference` is background.

## Mixed vintage — handle both shapes

Only documents screened under the new prompt have the new fields. Older ones emit
just `**What the document says:**` holding an **old-style blended summary** that may
name hydro without the document supporting it, and carry no Confidence line, no
read-through, and no sourced quotes.

Treat a **missing Confidence line as "unrated," not as low confidence.** Judge those
on their text, don't rank them last, and don't silently drop them from counts.

As of 2026-08-02 the week of Jul 27–31 is fully rescreened; the rest of the
Jul 17 – Aug 14 window is partly rescreened; everything older is old-style.
