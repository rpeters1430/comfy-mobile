# DESIGN.md — Image Studio Mobile

The phone UI follows **Material 3 Expressive** (dark scheme), implemented in plain CSS with no framework, web fonts or icon fonts. Styling lives in `app/static/style.css`; static markup in `app/static/index.html`; `app/static/app.js` builds workflow cards, job cards, gallery cards and controls. Read this before adding UI.

## Context

- One person on their own phone, one-handed, often over Tailscale. Jobs take minutes: *queue → leave → come back to results*.
- Installed as a PWA with a share target, mostly on Android. It should feel like a Pixel app, not a web page.
- Offline-on-LAN: the app must work when only the NAS is reachable, so nothing loads from a CDN.

## What "Expressive" means here

M3 Expressive adds four things on top of baseline Material 3. Each maps to something concrete in this app:

| Expressive idea | Here |
|---|---|
| **Contrasting shapes and shape morphing** | Buttons are pills that morph to a rounded square while pressed. Filter chips are rounded squares that become pills when selected. A selected workflow card morphs from 8px to 28px corners. |
| **Spring motion** | Spatial changes (shape, position, nav indicator, switch thumb, chevrons) use overshooting spring curves. Colour and opacity use non-bouncy effect curves. |
| **Emphasized type** | Headlines and titles use one weight step heavier than baseline M3 (600). |
| **Vibrant colour with clear hierarchy** | Tonal surface containers instead of borders and shadows; one warm tertiary accent for "special" things (video badge, Mobile badge). |

Use one or two expressive moments per screen. Don't make everything bounce.

## Colour roles

Tokens are `--md-*` on `:root`. Components use roles, never hex values. The scheme is an M3 dark scheme from an indigo seed.

| Role | Use |
|---|---|
| `surface` | Page background (also `theme-color` and the manifest colours) |
| `surface-container` | Cards (`.panel`), navigation bar |
| `surface-container-high` | Items inside cards: workflow cards, disclosures, slots, gallery cards, dialogs |
| `surface-container-highest` | Text fields, badges, captions |
| `on-surface` / `on-surface-variant` | Primary text / secondary text, labels, inactive icons |
| `primary` / `on-primary` | Filled button (Generate), switch track when on, focus ring, progress |
| `primary-container` / `on-primary-container` | Selected workflow, running job outline |
| `secondary-container` / `on-secondary-container` | Tonal buttons, selected chips, nav active indicator |
| `tertiary-container` / `on-tertiary-container` | Expressive accent: video badge, header badge |
| `error`, `success` (+ containers), `warning` | Status only. `success`/`warning` are app extensions, not M3 roles |
| `outline` / `outline-variant` | Switch outline / dividers, outlined buttons, unselected chips |
| `inverse-surface` / `inverse-on-surface` | Snackbar |

Always pair a container with its `on-` colour. To change the palette, regenerate the whole scheme from a new seed (Material Theme Builder) and replace the block; don't tweak single roles.

## Shape

| Token | px | Use |
|---|---|---|
| `--shape-sm` | 8 | Chips at rest, badges, middle workflow cards |
| `--shape-md` | 12 | Pressed buttons, snackbar |
| `--shape-lg` | 16 | Text fields, captions |
| `--shape-lg-inc` | 20 | Disclosures, slots, gallery cards, media, ends of the workflow list |
| `--shape-xl` | 28 | Cards (`.panel`), dialogs, selected workflow |
| `--shape-full` | pill | Buttons, selected chips, search bar, nav indicator, switches |

Grouped lists (the workflow list) use small inner corners and large outer corners, like M3 Expressive lists.

## Type

System stack `--font` (Google Sans Flex / Roboto on Android, system UI elsewhere). Set type with the `font` shorthand roles:

| Role | Spec | Use |
|---|---|---|
| `--type-headline-s` | 600 24/32 | Reserved for large page titles |
| `--type-title-l` | 600 22/28 | App bar title, card `h2` |
| `--type-title-m` | 600 16/24 | Workflow names, disclosure titles, Generate |
| `--type-body-l` | 400 16/24 | Body, inputs, switch labels |
| `--type-body-m` | 400 14/20 | Help text (`.status`), metadata |
| `--type-label-l` | 500 14/20 | Buttons, chips, field labels |
| `--type-label-m` | 500 12/16 | Badges, nav labels |

Inputs stay at 16px or larger so iOS doesn't zoom. Labels are sentence case, never uppercase. Numbers that change use `tabular-nums`.

## Motion

| Token | Curve | Duration | Use |
|---|---|---|---|
| spatial-fast | `cubic-bezier(0.42, 1.67, 0.21, 0.9)` | 350ms | Button/chip shape morph, switch thumb, nav indicator |
| spatial | `cubic-bezier(0.38, 1.21, 0.22, 1)` | 500ms | Disclosure chevron, dialog entrance, pulse |
| effects | `cubic-bezier(0.34, 0.8, 0.34, 1)` | 200ms | Colour, opacity, state layers, focus |

The beziers approximate M3 Expressive's springs (they overshoot). Animate only `transform`, `opacity`, colour and `border-radius`; never width/height/top/left. `prefers-reduced-motion` turns all of it off.

## Elevation and state

- Dark theme hierarchy comes from tonal surfaces, not shadows. Only floating UI (sticky Generate, snackbar) gets `--elev-3`.
- **State layers:** every button has a `::after` overlay of `currentColor`: 8% on hover (only under `@media (hover: hover)`), 12% pressed. Don't fake states with opacity on the whole control.
- Disabled is 38% opacity. Focus is a 3px `primary` outline (`:focus-visible`); fields show an inset 2px `primary` ring.

## Components

| Component | Markup | Notes |
|---|---|---|
| Top app bar | `header` | Title-large, status line under it, one tonal action. |
| Card | `.panel` | One topic per card, `h2` title. |
| Filled button | `.primary` | One per screen: Generate, "Fix & generate". 56px tall. Generate is sticky above the nav bar. |
| Tonal button | `.secondary` | Default action in a row. |
| Outlined button | `.ghost` | Low emphasis, cancel, recoverable delete. Icon-only needs `aria-label`. |
| Filter chip | `.chip` + `aria-pressed` | Kind filter, aspect, wallpaper style, caption switches. Square→pill when selected. |
| List item | `.wf` + `aria-pressed` | Grouped in `.wf-list`. Selected = `primary-container` and 28px corners. |
| Badge | `.badge` (`.video`, `.synced`, `.ready`) | Not interactive. |
| Search bar | `.wf-search-wrap` | Pill, leading icon. |
| Text field | `input`, `select`, `textarea` | Filled, 56px, label above (`<label for>`). The base rule uses `:where()` so component rules override it. |
| Switch | `<label class="switch"><input type="checkbox"> Text</label>` | Settings toggles. Thumb grows and slides on a spring. Text leads, switch trails. |
| Checkbox | `<label><input type="checkbox"> Text</label>` | Choosing items from a list (check-dialog fixes, adviser suggestions). Not for settings. |
| Disclosure | `<details class="controls">` | Expandable card; every `summary` gets a spring-rotating chevron. |
| Image slot | `.slot` | Dashed 20px-corner tile, file input fills it. |
| Job card | `.panel.job` (`.running`) | Running = `primary-container` outline + pulse. |
| Gallery | `.gallery > .card` | Newest job spans full width as a hero; the rest form a 2-column grid. |
| Navigation bar | `nav > button > .nav-icon + label` | 80px. Active: pill indicator scales in, icon fills, label bolds. Max 3–5 destinations. |
| Snackbar | `#toast` | Inverse surface, bottom, above the nav bar. Short confirmations only. |
| Dialog | `#check-dialog` | 28px corners, scrim, scale-in. `#image-viewer` is full screen. |

## Icons

Inline SVG, 24px, 2px rounded stroke (`.icon`), `aria-hidden="true"` beside visible text. Active nav icons switch to filled (`fill: currentColor`). Keep new icons in the same stroke style; don't use emoji as structural icons in nav, buttons or app chrome. Emoji in user content (chip labels from presets) are fine.

## Layout

- 16px gutter, 12px between cards, 20px card padding, 8px between grouped items.
- Single 680px column; grids use `auto-fill, minmax()` instead of breakpoints.
- Fixed and sticky elements clear `--nav-h` plus `env(safe-area-inset-bottom)`.
- Touch targets are at least 44px (48px for primary controls).

## Copy

Sentence case. Buttons are verbs. Status lines say what happened ("Synced 3 workflows"). Use the user's words ("ComfyUI PC", "NAS", "this device"), not internal ones (prompt id, node id).

## Accessibility checklist

- Every field has a visible label; async status uses `role="status"`, blocking errors `role="alert"`.
- Toggles use `aria-pressed`; nav uses `aria-current="page"`.
- Text contrast is at least 4.5:1 (`on-surface-variant` on `surface-container-high` is about 8:1). Don't add text dimmer than `on-surface-variant`.
- Never convey state by colour alone: selected chips also change shape, active nav also fills its icon and bolds its label.

## Don'ts

- No raw hex in component rules, no new colours outside the scheme, no light theme without generating the light scheme.
- No borders to separate surfaces; use the next container tone.
- No inline `style=""` for reusable styling.
- No CDN assets, web fonts or icon fonts.
- No horizontal page scroll; only `.chips` scrolls sideways.
