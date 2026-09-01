# Sentinel CCTV Registry — Product context

## Register

**Product.** Design serves the task. This is an authenticated operations tool: data
tables, forms, a map, an import wizard. Nobody is browsing it.

## Users & purpose

A district administrator or duty officer, at a desk in a Gujarat government
building, during working hours. On 10 September 2026 it is also projected in a
judging room, which is why the theme is light: projectors wash out dark themes,
and a demo nobody can read is a demo that failed.

The jobs, in order of frequency:

1. **"Which cameras are down, and for how long?"** — before an operation, before a
   procurement meeting, after a complaint.
2. **"What cameras exist near here?"** — an incident location, a route, a district.
3. **"Onboard this department's list"** — a spreadsheet from a department that has
   never shared data before, in a format nobody agreed on.
4. **"Why is this camera here?"** — provenance: who added it, when, from what source.

They are not analysts. They are people with a question and limited patience, using
this between other work.

## Personality

**Quiet instrument.** Three words: precise, calm, legible.

The interface is near-monochrome **so that colour means something**. Red is a
camera that is down. Amber is one in maintenance. Green is one that is watching.
If the chrome also uses red and green decoratively, the status column stops being
readable at a glance — and that column is the product.

Density is a feature. These users would rather see forty rows than twelve with
generous padding.

## Anti-references

- **Generic Tailwind SaaS dashboard.** Rounded-2xl cards in a three-column grid,
  gradient stat tiles, purple accent. The saturated default.
- **2010s government portal.** Seals, heavy navy chrome, formal serif headings,
  everything boxed.
- **Decorative colour.** Any hue used because a section needed visual interest.
  Colour is reserved for state and for the primary action.
- **Marketing motion.** Scroll reveals, staggered page-load choreography. Users
  load into a task.

## Strategic design principles

1. **Colour is signal, never decoration.** The palette is deliberately narrow so
   the status vocabulary reads instantly.
2. **Identifiers are monospaced.** Camera UIDs, coordinates, stream URLs, API keys
   and audit diffs are scanned and compared, not read. That is a functional
   pairing, not a stylistic one.
3. **Say what happened and what to do.** Every error names the field and the
   reason. Every empty state teaches the next action.
4. **Show the caveat where the number is.** District-level positions and
   unrecognised vocabulary values are surfaced on the record, not buried in a
   report footnote. A figure presented without its assumptions invites a decision
   it cannot support.
5. **Earned familiarity.** Standard affordances, consistently. The tool should
   disappear into the task.

## Accessibility

Body text ≥4.5:1. Status is never colour alone — every badge carries its word.
Keyboard reachable throughout; visible focus rings. `prefers-reduced-motion`
honoured.
