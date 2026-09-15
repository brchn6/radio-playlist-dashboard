# Section tags

Every labelled region of the dashboard carries a stable English id, rendered in
its upper-left corner. They exist so a human can point an agent at **one exact
block** of the page instead of describing it:

> "the redundancy thing repeats itself weirdly" -> "fix `deep:redundancy`"

Format is `subject:thing`, lowercase, one colon: `insights:top-songs`.

- **Click a tag** to copy its id (a confirmation chip appears at the bottom).
  Over plain http (the LAN and Tailscale previews) the clipboard API is
  unavailable, so the page falls back to a selection-based copy.
- **They are visible to everyone** by Bar's decision (2026-09-15): the page is a
  public tool, and a label you cannot see from a phone is a label you cannot use.
- On phones and tablets (<= 768px) a corner tag would land on top of the Hebrew
  section titles, so every tag takes a line of its own, flush left, above its
  region. Above 768px the tags sit in the corner and cost no vertical space.

## Registry

`docs/index.html` is the source; this table is the contract. The per-station
family is generated, so it is documented as a pattern.

| Tag | Placement | Labels | Rendered by |
|---|---|---|---|
| `shell:header` | flow | Top bar: status dot, title, stats pill, theme toggle | static markup |
| `shell:tabs` | flow | The three tab buttons (now / insights / deep) | static markup |
| `shell:stations` | inline | Station filter pill row (insights and deep only) | `renderPills()` |
| `shell:footer` | corner | Footer stats line | static markup |
| `now:stations` | flow | Now Playing grid, `#npGrid` | static markup |
| `now:station-<slug>` | corner | One card per station, e.g. `now:station-kan-88` | `renderNowPlaying()` |
| `insights:top-songs` | corner | Top songs card, including its window pills | `renderTopSongs()` |
| `insights:top-artists` | corner | Top artists card, including its window pills | `renderTopArtists()` |
| `insights:cross-station` | corner | Cross-station card (songs that jump between stations) | `renderCrossStation()` |
| `insights:history` | corner | History card: search box, unique/newest toggles | `renderHistory()` |
| `insights:history-list` | flow | The history rows and the "show more" button | `renderHistory()`, `loadMoreHistory()` |
| `deep:bpm` | corner | BPM flow chart card | `renderBpmRealtime()` |
| `deep:keys` | corner | Key (major/minor) distribution card | `renderKeyDistRealtime()` |
| `deep:transitions` | corner | Transition Explorer card: controls and results | `renderTransitionExplorer()` |
| `deep:transitions-results` | flow | Explorer results region below the controls | `renderTransitionExplorer()` |
| `deep:transitions-chain` | flow | The "where to next?" outgoing-transition chain | `renderTransitionExplorer()` |
| `deep:clusters` | corner | Co-play graph card | `renderClusters()` |
| `deep:redundancy` | corner | Repetition card (how often each station repeats itself) | `renderRedundancy()` |
| `deep:uptime` | corner | System operations card (uptime, collector status) | `renderUptime()` |

## Placement variants

| Variant | CSS | Behaviour |
|---|---|---|
| corner (default) | `.sec-tag` | Absolutely positioned in the region's top-left corner. The region must be `position: relative` (`.card` and `.np-card` are). |
| flow | `.sec-tag-flow` | Takes a line of its own, flush to the visual left, for regions whose top-left corner already holds content. |
| inline | `.sec-tag-inline` | Rides along a control row (the station pills). |

## Maintenance

- **Adding a region means adding a tag**, and the tag goes in this table. If the
  two drift apart, `tests/verify_section_tags.js` fails: it reconciles the
  registry against the page in both directions.
- Tag ids are **stable**: if a region is renamed in the UI, keep the id. An id
  that moves is an id nobody can rely on.

## Verify

```bash
node tests/verify_section_tags.js                  # the local file
node tests/verify_section_tags.js /tmp/live.html   # a deployed copy
```

The suite checks the registry against the page, the `subject:thing` shape, that
static `data-sec` ids are unique, and drives the real `secTag()`,
`copySectionTag()`, `renderPills()` and `renderNowPlaying()` to prove the copy
path works, including the insecure-context fallback.
