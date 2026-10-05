import { test, expect } from '@playwright/test';

const chipMarkup = (n: number) =>
	`<span class="chip phrase-chip"><button type="button" class="chip-main"><span class="chip-mark">#</span><span class="chip-context">Lighting</span><span class="chip-label">Golden hour backlight ${n}</span></button></span>`;

test('phrasebook chips on consecutive wrapped lines do not overlap', async ({ page }) => {
	await page.goto('/');

	await page.evaluate((markup) => {
		const host = document.createElement('div');
		host.id = 'chip-spacing-host';
		host.className = 'composer';
		host.style.cssText = 'width: 320px; position: fixed; top: 40px; left: 40px;';
		host.innerHTML = `<div class="segment-content"><div class="inline-chip-editor" style="white-space: pre-wrap; word-break: break-word;">${markup}</div></div>`;
		document.body.appendChild(host);
	}, [1, 2, 3, 4].map(chipMarkup).join(' '));

	const boxes = await page.$$eval('#chip-spacing-host .chip', (els) =>
		els.map((el) => {
			const r = el.getBoundingClientRect();
			return { top: r.top, bottom: r.bottom, left: r.left, right: r.right };
		})
	);
	expect(boxes.length).toBe(4);

	const tops = new Set(boxes.map((b) => Math.round(b.top)));
	expect(tops.size).toBeGreaterThan(1);

	for (let i = 0; i < boxes.length; i++) {
		for (let j = i + 1; j < boxes.length; j++) {
			const a = boxes[i];
			const b = boxes[j];
			const intersects = a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
			expect(intersects, `chip ${i} and ${j} intersect`).toBe(false);
		}
	}

	const lineHeight = await page.$eval('#chip-spacing-host .inline-chip-editor', (el) =>
		parseFloat(getComputedStyle(el).lineHeight)
	);
	for (const b of boxes) expect(b.bottom - b.top).toBeLessThanOrEqual(lineHeight);
});
