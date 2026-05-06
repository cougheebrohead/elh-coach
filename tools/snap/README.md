# Marketing screenshot pipeline

Headlessly drives the live `app.html` + `client.html` SPAs against
mocked fetch responses, then captures each marketing-page screenshot
via WKWebView. Used to keep `/screenshots/*.png` in sync whenever the
SPA UI or the demo brand changes.

## Run it

```bash
./tools/snap/snap_all.sh
```

Six PNGs land in `screenshots/`:

| File                       | View                                  | Logical px   |
|----------------------------|---------------------------------------|--------------|
| `coach_dashboard.png`      | Coach Portal · Dashboard tab          | 1280 × 700   |
| `coach_roster.png`         | Coach Portal · Roster tab             | 1280 × 920   |
| `coach_programs.png`       | Coach Portal · Programs tab           | 1280 × 720   |
| `coach_client_labs.png`    | Coach Portal · client drilldown, Labs | 1280 × 1100  |
| `client_today.png`         | Client app · Today screen             |  400 × 950   |
| `client_lab_snap.png`      | Client app · Snap-a-lab modal review  |  414 × 900   |

PNGs render at 2× (Retina), so on disk you'll see e.g. 2560 × 1400.

## Files

- `snap.swift` — WKWebView screenshot tool, `swift snap.swift <html> <out.png> <w> <h> [postLoadJS]`.
- `mock_inject.py` — replaces `<!--BRAND_INJECT-->` in a copy of each SPA with a mock fetch + brand override (currently set to the ELH Coach demo brand). Reads the destination dir from `$SNAP_BUILD_DIR`.
- `snap_all.sh` — orchestrates copy-from-repo → inject → 6 snaps.

## Adding a screenshot

1. Pick a target view in the SPA, work out a `postLoadJS` snippet that drives the SPA into that state.
2. Add a `run` line in `snap_all.sh`. Match the existing dimensions used in `marketing.html` (run `sips -g pixelWidth -g pixelHeight <png>` and halve them — they're 2×).
3. Reference the new PNG from `marketing.html` with appropriate `alt` text.

## Updating the demo brand

Edit `mock_inject.py` — both the `COACH_MOCK` and `CLIENT_MOCK` `window.__BRAND__` blocks, plus the seed user / tenant payloads. Re-run `snap_all.sh`.
