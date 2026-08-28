// Renders the PotionUI brand kit (marks, avatars, lockups, banner) from the
// canonical 48x48 "Inlets" mark via Chromium, so output matches the app pixel
// for pixel. Run with cwd=frontend so `playwright` resolves.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const OUT = process.env.BRAND_OUT;
const SANS = process.env.SANS_WOFF2; // IBM Plex Sans 600 (Potion)
const MONO = path.resolve('static/fonts/ibm-plex-mono-500.woff2'); // UI

const LIGHT = '#EDEDED'; // mark on dark surfaces
const DARK = '#171717'; // mark on light surfaces
const CANVAS_DARK = '#0C0D0F'; // app dark canvas token
const CANVAS_LIGHT = '#F7F7F7'; // app light canvas token

const markSvg = (color, size) => `
<svg width="${size}" height="${size}" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
  <circle cx="24" cy="24" r="8.5" fill="${color}"/>
  <g fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round">
    <path d="M27.51 9.93 A14.5 14.5 0 0 1 37.94 28.0"/>
    <path d="M34.43 34.07 A14.5 14.5 0 0 1 13.57 34.07"/>
    <path d="M10.06 28.0 A14.5 14.5 0 0 1 20.49 9.93"/>
  </g>
  <g fill="${color}">
    <circle cx="24" cy="6" r="1.7"/>
    <circle cx="39.6" cy="33" r="1.7"/>
    <circle cx="8.4" cy="33" r="1.7"/>
  </g>
</svg>`;

const b64 = (p) => fs.readFileSync(p).toString('base64');
const fontCss = `
@font-face { font-family:'IBM Plex Sans'; font-weight:600; src: url(data:font/woff2;base64,${b64(SANS)}) format('woff2'); }
@font-face { font-family:'IBM Plex Mono'; font-weight:500; src: url(data:font/woff2;base64,${b64(MONO)}) format('woff2'); }
`;

// Mirrors LogoLockup.svelte: gap size*0.34, word size*0.6, UI 0.82em mono.
const lockupHtml = (color, size) => `
<style>${fontCss}
  * { margin:0; padding:0; }
  body { background: transparent; }
  .lockup { display:inline-flex; align-items:center; gap:${size * 0.34}px; line-height:1; padding: ${size * 0.1}px; }
  .word { font-size:${size * 0.6}px; white-space:nowrap; color:${color}; }
  .potion { font-family:'IBM Plex Sans', sans-serif; font-weight:600; letter-spacing:-0.015em; }
  .ui { font-family:'IBM Plex Mono', monospace; font-weight:500; font-size:0.82em; letter-spacing:0.12em; margin-left:0.09em; }
</style>
<span class="lockup" id="target">${markSvg(color, size)}<span class="word"><span class="potion">Potion</span><span class="ui">UI</span></span></span>`;

const centeredHtml = (bg, inner, w, h) => `
<style>${fontCss}
  * { margin:0; padding:0; }
  body { width:${w}px; height:${h}px; background:${bg}; display:flex; align-items:center; justify-content:center; }
</style>${inner}`;

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage();

  const shootPage = async (html, w, h, file, { transparent = false } = {}) => {
    await page.setViewportSize({ width: w, height: h });
    await page.setContent(html, { waitUntil: 'networkidle' });
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: path.join(OUT, file), omitBackground: transparent });
    console.log('wrote', file);
  };

  // 1) Transparent marks, both inks.
  for (const [name, color] of [['light', LIGHT], ['dark', DARK]]) {
    for (const size of [256, 512, 1024]) {
      await shootPage(
        centeredHtml('transparent', markSvg(color, size), size, size),
        size, size, `potionui-mark-${name}-${size}.png`, { transparent: true },
      );
    }
  }

  // 2) Solid avatars (circle-crop safe: mark at 58% of the tile).
  for (const [name, bg, ink] of [['dark', CANVAS_DARK, LIGHT], ['light', CANVAS_LIGHT, DARK]]) {
    for (const size of [512, 1024]) {
      const mark = Math.round(size * 0.58);
      await shootPage(
        centeredHtml(bg, markSvg(ink, mark), size, size),
        size, size, `potionui-avatar-${name}-${size}.png`,
      );
    }
  }

  // 3) Transparent lockups (tight element screenshot).
  for (const [name, color] of [['light', LIGHT], ['dark', DARK]]) {
    await page.setViewportSize({ width: 2400, height: 600 });
    await page.setContent(lockupHtml(color, 320), { waitUntil: 'networkidle' });
    await page.evaluate(() => document.fonts.ready);
    const el = await page.$('#target');
    await el.screenshot({ path: path.join(OUT, `potionui-lockup-${name}.png`), omitBackground: true });
    console.log('wrote', `potionui-lockup-${name}.png`);
  }

  // 4) Banner (dark canvas, centered lockup) — Reddit/community headers.
  const bannerBody = `
    ${lockupHtml(LIGHT, 160)}
    <style>
      body { width:1920px; height:480px; background:${CANVAS_DARK}; display:flex; align-items:center; justify-content:center; }
    </style>`;
  await shootPage(bannerBody, 1920, 480, 'potionui-banner-dark.png');

  await browser.close();
})();
