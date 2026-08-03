# Barry Sentinel V7 — Barry Moneycome WOW Cockpit V2

Read-only visual cockpit for the private Barry Sentinel V7 Paper/Shadow backend.

- Local V7 status bridge
- Five-symbol decision radar
- Capital/risk planning stored in browser localStorage
- Market/session clocks
- Position P/L and performance panels
- No broker-order execution in this package
- Live trading remains blocked

Start locally with `START_BARRY_COCKPIT.bat`.


## Readable V3

This revision focuses on desktop readability and a true mobile layout. Trading logic is unchanged.

## Readable Sticky V4

- Desktop/tablet header + ticker are frozen while scrolling.
- System Feed typography is significantly larger.
- Mobile intentionally keeps the large header non-sticky to preserve usable screen height.
- Trading/backend logic is unchanged.

## GitHub Safe V5

The deployed dashboard is now self-contained in `index.html`.
CSS and JavaScript are inlined to avoid missing-asset problems on GitHub Pages.
The local Python backend workflow remains supported.
