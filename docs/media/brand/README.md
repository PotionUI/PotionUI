# PotionUI brand kit

High-resolution exports of the "Inlets" mark for use outside the app —
community avatars, banners, posts. All PNGs are rendered from the canonical
48×48 SVG (`frontend/static/brand/logo.svg`) via `render-brand.cjs`
(Chromium/Playwright), so they match the app pixel for pixel.

| File | Use |
| --- | --- |
| `potionui-avatar-dark-{512,1024}.png` | Profile/community avatar (Reddit, CivitAI, GitHub org, Discord). Dark canvas `#0C0D0F`, mark at 58% — safe under circular crops. |
| `potionui-avatar-light-{512,1024}.png` | Same, for platforms that render avatars on light UI. |
| `potionui-mark-light-{256,512,1024}.png` | Transparent mark in `#EDEDED` — place on dark backgrounds. |
| `potionui-mark-dark-{256,512,1024}.png` | Transparent mark in `#171717` — place on light backgrounds. |
| `potionui-lockup-{light,dark}.png` | Transparent mark + "PotionUI" wordmark (IBM Plex Sans 600 / Plex Mono 500, as in the app). |
| `potionui-banner-dark.png` | 1920×480 header/banner on the dark canvas. |

Rules (same as in-app): the mark is monochrome — never recolor per-element,
distort, or add effects. White is `#EDEDED`, black is `#171717`, canvases are
`#0C0D0F` / `#F7F7F7`.

To regenerate (needs `frontend/node_modules` and a Playwright Chromium;
IBM Plex Sans 600 woff2 is fetched separately since the app only self-hosts
Plex Mono):

```bash
cd frontend
SANS_WOFF2=/path/to/ibm-plex-sans-600.woff2 \
BRAND_OUT=../docs/media/brand \
NODE_PATH=$(pwd)/node_modules \
node ../docs/media/brand/render-brand.cjs
```
