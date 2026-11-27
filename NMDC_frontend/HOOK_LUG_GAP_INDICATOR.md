# Hook–Lug Gap Indicator

`src/components/HookLugGapIndicator.jsx` is a reusable, stationary indicator:

```jsx
<HookLugGapIndicator gapPercent={36.7} isEngaged={false} />
```

- `gapPercent`: a finite numeric remaining-gap percentage. Values are clamped to
  0–100; missing, nonnumeric and nonfinite values show `—% / NO GAP DATA`.
- `isEngaged`: only literal `true` confirms engagement. It takes precedence over
  the gap value and shows `0.0% / ENGAGED`, with the two segments meeting.
- `label`: optional accessible label, used to identify the camera.
- Red: gap greater than 40%. Amber: gap at or below 40%, including zero when
  engagement is not confirmed. Green is reserved for confirmed engagement.
  These display thresholds are visual cues, not detection or lifting rules.

## Live data wiring

Each existing `CameraViewer` polls its own `/camera/<camera-id>/status` through
`useDetectorFeed` and `services/api.js`. The service preserves backend fields.
The new indicator reuses that response; it adds no requests or polling timers.

`CameraViewer` passes `data.gap_percent` as `gapPercent` and the existing verified
`hook_engaged` or `lift_ready` flag as `isEngaged`. Both are cleared from the
display when the camera is disconnected. Engagement continues to be determined
by the backend's cavity/hook/lock detection and existing lock-readiness latch.

The current Python backend does **not** publish a measured gap percentage.
Therefore live indicators show no gap measurement until a finite numeric
`gap_percent` is supplied by a calibrated backend implementation. Verified
engagement can still display `0.0% / ENGAGED` without a gap measurement. The
existing `meter`, `offset`, alignment labels and bounding-box scores are **not**
converted into invented percentages. No backend endpoints or detection logic
were changed for this feature.

Future measured data should be included in the existing camera status response:

```json
{"gap_percent": 36.7, "hook_engaged": false, "lift_ready": false}
```

Each detailed camera has a scaled version of the supplied reference indicator
in the bottom-left corner: 210 pixels tall on desktop and up to 148 pixels tall on small screens. The
width fits the icons, bar and percentage with a small inset, and small-screen
height is capped to leave the warning overlays clear. It includes a dark card, white hook and lug icons, a rounded two-ended
vertical bar and a small percentage only. Captions, status wording and the
top-right engagement badge are hidden in the live camera presentation; the
accessible description retains the verified status.
The previously removed NMDC branding remains omitted. It does not use a sidebar,
change the video width or capture pointer events. The large standalone demo
uses the same indicator and verified-engagement behavior.


## Separate demo

From `frontend`, run `npm.cmd run dev`, then open:

`http://localhost:4173/gap-indicator-demo.html`

This standalone development page is prominently labeled **DEMO · SIMULATED
DATA**, with manual gap and engagement controls. A gap of zero does not check the
engagement flag. It is not imported into the production app or bundled by the
default production build, and makes no camera requests.

## Animation and validation

Only segment transforms/colors and the numeric readout update. The readout uses
one short, cancelable `requestAnimationFrame` animation when its target changes;
it does not rerender or poll the camera on each animation frame. Invalid data
clears the readout immediately. Confirmed engagement immediately sets the numeric
value to zero. Reduced-motion preferences disable value and bar interpolation.
The accessible label always reports the authoritative target rather than
transitional numbers.

Build: `npm.cmd run build`. This project currently has no lint script or lint
configuration. The demo can be exercised separately with the Vite dev server.
