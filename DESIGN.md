# Sentinel CCTV Registry — Design system

## Theme

Light. Near-monochrome chrome with one brand accent and a strict status palette.

**Colour strategy: Restrained.** The accent appears on primary actions, the current
nav item, and focus rings. Nowhere else. Status colours appear only on camera
state. The result is that a red badge in a table of eighty thousand rows is
genuinely alarming, because nothing else on the page is red.

## Palette (OKLCH)

Neutrals carry a faint cool tint toward the brand hue (chroma 0.004–0.012, hue
250) rather than the warm/cream tint that has become the AI default.

| Token | Value | Use |
|---|---|---|
| `--bg` | `oklch(0.985 0.003 250)` | Page background |
| `--surface` | `oklch(1 0 0)` | Panels, table bodies, inputs |
| `--surface-sunken` | `oklch(0.968 0.005 250)` | Table headers, toolbars, nav |
| `--border` | `oklch(0.905 0.006 250)` | Hairlines |
| `--border-strong` | `oklch(0.845 0.008 250)` | Input borders, dividers that matter |
| `--ink` | `oklch(0.235 0.012 250)` | Body text — 13.8:1 on `--bg` |
| `--ink-muted` | `oklch(0.455 0.011 250)` | Secondary text — 6.9:1, still passes |
| `--ink-faint` | `oklch(0.544 0.010 250)` | Labels, hints — 4.5:1 on every surface, verified |
| `--brand` | `oklch(0.398 0.104 255)` | Gujarat Police navy. Primary action, selection |
| `--brand-hover` | `oklch(0.338 0.104 255)` | |
| `--brand-tint` | `oklch(0.955 0.020 255)` | Selected row, active nav background |
| `--focus` | `oklch(0.556 0.152 255)` | Focus ring, 3px, always visible |

### Status — the only other colour on the page

| State | Text | Background |
|---|---|---|
| online | `oklch(0.418 0.108 150)` | `oklch(0.944 0.038 150)` |
| offline | `oklch(0.478 0.176 25)` | `oklch(0.946 0.040 25)` |
| maintenance | `oklch(0.478 0.108 75)` | `oklch(0.948 0.052 75)` |
| unknown | `--ink-muted` | `--surface-sunken` |

Every badge carries its word as well as its colour.

## Typography

One family plus a mono, on a genuine contrast axis rather than two similar sans.

- **Geist** — all UI text. Already loaded via `next/font`; no new network cost.
- **Geist Mono** — identifiers only: camera UIDs, external IDs, coordinates, stream
  URLs, API key prefixes, config JSON, audit field names. These are scanned and
  compared character by character, so a fixed advance width is functional.

Fixed rem scale, ratio ≈1.14 — product UI is viewed at consistent DPI, and fluid
headings that shrink inside a panel look worse, not better.

| Step | Size / line-height | Use |
|---|---|---|
| `--text-2xs` | 0.6875rem / 1.4 | Table header labels, badges |
| `--text-xs` | 0.75rem / 1.5 | Hints, metadata |
| `--text-sm` | 0.8125rem / 1.55 | Table cells, form controls |
| `--text-base` | 0.9375rem / 1.6 | Body |
| `--text-lg` | 1.0625rem / 1.4 | Section headings |
| `--text-xl` | 1.375rem / 1.3 | Page titles |
| `--text-2xl` | 1.75rem / 1.2 | Metric values |

Numerals use `font-variant-numeric: tabular-nums` everywhere a figure can change
or be compared, so columns do not jitter on refresh.

## Spacing & shape

4px base. Radius is small and consistent: `4px` controls, `6px` panels, `999px`
badges only. No `rounded-2xl` — soft corners at this density read as consumer
software.

Elevation is carried by border and background, not shadow. One shadow exists, for
the popover layer.

## Components

Every interactive element ships default, hover, focus-visible, active, disabled
and, where it loads, a busy state.

- **Panel** — bordered surface. Never nested.
- **Button** — `primary` (brand), `default` (bordered), `subtle`, `danger`. One
  height (32px), one radius.
- **Badge** — status vocabulary; always word + colour.
- **DataTable** — sunken sticky header, hairline rows, hover tint, tabular figures.
- **Field** — label, control, hint, error. Errors name the field and the reason.
- **EmptyState** — says what would appear here and what to do next.
- **Skeleton** — for table and panel loading. Not a centred spinner.
- **Toolbar** — sunken strip holding filters and actions above a table.

## Motion

160–200 ms, `cubic-bezier(0.16, 1, 0.3, 1)` (ease-out-expo). Motion conveys state
only: hover, focus, panel open, row appear, busy. No page-load choreography.

Under `prefers-reduced-motion: reduce`, transitions collapse to 1 ms and the map's
`flyTo` becomes `jumpTo`.

## Z-index scale

`--z-sticky: 10`, `--z-dropdown: 20`, `--z-overlay: 30`, `--z-modal: 40`,
`--z-toast: 50`. No arbitrary values.
