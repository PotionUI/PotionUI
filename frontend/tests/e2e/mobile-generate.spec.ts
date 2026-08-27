import { test, expect } from '@playwright/test';
import { loginAsOwner, screenshot, ownerToken } from './helpers';

const JOURNEY = 'mobile-generate';

// Phone-viewport shell contract for the generate page. Guards the class of
// regression where the mobile carousel drifts out of alignment (panels sized
// in vw instead of container %), the page overflows behind the fixed bottom
// tab bar (100vh shells inside a pb-reserved main), or the transport bar /
// install banner stack in the wrong order.
test.use({ viewport: { width: 375, height: 812 }, hasTouch: true });

test('generate page fits and aligns at phone width', async ({ page }) => {
	await loginAsOwner(page);
	await page.goto('/generate');

	// Mobile chrome is up: panel strip + bottom tab bar, no desktop sidebar.
	const panelStrip = page.getByRole('region', { name: 'Swipeable panels' });
	await expect(panelStrip).toBeVisible({ timeout: 15000 });
	await expect(page.locator('nav').filter({ hasText: 'Generate' }).last()).toBeVisible();

	// No page-level scrolling in either axis: horizontal overflow was how every
	// layout bug degraded (main is a scroller), and vertical overflow means the
	// shell slid behind the fixed tab bar. scrollbar-gutter can make scrollWidth
	// come up SHORT of clientWidth, so assert "no overflow", not equality.
	const { overflowX, overflowY } = await page.evaluate(() => {
		const el = document.scrollingElement!;
		return {
			overflowX: el.scrollWidth - el.clientWidth,
			overflowY: el.scrollHeight - el.clientHeight
		};
	});
	expect(overflowX, 'page must not scroll horizontally').toBeLessThanOrEqual(0);
	expect(overflowY, 'page shell must fit the viewport (no scroll behind the tab bar)').toBeLessThanOrEqual(0);

	// The active panel (Generate, index 2) is exactly aligned with the swipe
	// container: same left edge, same width. This is the regression the old
	// 100vw-sized panels failed — they were wider than the container by the
	// scrollbar gutter, bleeding each panel into the next.
	const panels = panelStrip.locator('.mobile-panel');
	await expect(panels).toHaveCount(4);
	const containerBox = await panelStrip.boundingBox();
	const box = await panels.nth(2).boundingBox();
	expect(box, 'active panel has a box').toBeTruthy();
	expect(Math.abs(box!.x - containerBox!.x), 'active panel aligned with container').toBeLessThanOrEqual(1);
	expect(Math.abs(box!.width - containerBox!.width), 'panel spans the container').toBeLessThanOrEqual(1);

	await screenshot(page, JOURNEY, 'generate-panel');

	// Switching to the Form panel keeps alignment and keeps the transport bar
	// (the mobile Generate button) visible.
	await page.getByRole('button', { name: 'Form' }).click();
	await expect(page.getByRole('button', { name: /Generate|Cancel generation|generate/i }).last()).toBeVisible();
	// Let the 300ms slide transition settle before measuring alignment.
	await page.waitForTimeout(500);
	const formBox = await panels.nth(1).boundingBox();
	expect(Math.abs(formBox!.x - containerBox!.x), 'form panel aligned with container').toBeLessThanOrEqual(1);

	await screenshot(page, JOURNEY, 'form-panel');

	// Regression: the browser fires touchcancel (not touchend) when it takes a
	// gesture over mid-swipe — long-press selection, notification shade, the
	// screenshot gesture. The stranded drag delta must not freeze the carousel
	// at a fractional offset, and tapping the panel strip (which lives outside
	// the swipe container) must always land exactly aligned.
	await page.evaluate(() => {
		const region = document.querySelector('[aria-label="Swipeable panels"]')!;
		const touch = (x: number) =>
			new Touch({ identifier: 1, target: region, clientX: x, clientY: 300 });
		const fire = (type: string, touches: Touch[]) =>
			region.dispatchEvent(
				new TouchEvent(type, { bubbles: true, cancelable: true, touches, changedTouches: touches })
			);
		fire('touchstart', [touch(300)]);
		fire('touchmove', [touch(100)]); // 200px left drag — swipe latched
		fire('touchcancel', []);
	});
	await page.getByRole('button', { name: 'Generate', exact: true }).first().click();
	await page.waitForTimeout(500);
	const recoveredBox = await panels.nth(2).boundingBox();
	expect(
		Math.abs(recoveredBox!.x - containerBox!.x),
		'carousel realigned after touchcancel + strip tap'
	).toBeLessThanOrEqual(1);

	// The bottom tab bar must sit fully inside the viewport.
	const tabBar = page.locator('nav.fixed.bottom-0');
	const tabBox = await tabBar.boundingBox();
	expect(tabBox!.y + tabBox!.height, 'tab bar bottom edge on-screen').toBeLessThanOrEqual(812 + 1);
	expect(tabBox!.y, 'tab bar visible above viewport bottom').toBeGreaterThan(700);
});

test('carousel recovers from a stray scrollLeft on the swipe container', async ({ page }) => {
	// Regression: the off-screen LLM chat panel autofocuses its composer on
	// mount, and a browser's default focus scroll-into-view sets scrollLeft on
	// the nearest clipping ancestor even though that container has no visible
	// scrollbar and is never meant to scroll under user control. That offset
	// used to latch forever because the carousel's translateX math never
	// accounted for it. Simulates the same effect directly (any focus deep in
	// an off-screen panel would set scrollLeft the same way).
	await loginAsOwner(page);
	await page.goto('/generate');

	const panelStrip = page.getByRole('region', { name: 'Swipeable panels' });
	await expect(panelStrip).toBeVisible({ timeout: 15000 });
	const panels = panelStrip.locator('.mobile-panel');
	await expect(panels).toHaveCount(4);
	const containerBox = await panelStrip.boundingBox();

	await page.evaluate(() => {
		const region = document.querySelector('[aria-label="Swipeable panels"]')!;
		region.scrollLeft = 200;
	});
	await page.waitForTimeout(200);

	const scrollLeft = await page.evaluate(
		() => document.querySelector('[aria-label="Swipeable panels"]')!.scrollLeft
	);
	expect(scrollLeft, 'swipe container scroll position resets itself').toBe(0);

	// Active panel (Generate, index 2, the default) still lines up exactly.
	const box = await panels.nth(2).boundingBox();
	expect(Math.abs(box!.x - containerBox!.x), 'active panel realigned after stray scroll').toBeLessThanOrEqual(1);
});

test('overlay opened from inside a carousel panel covers the full viewport', async ({ page }) => {
	// Regression: .mobile-panels-track carries a translateX transform, which
	// becomes the containing block for any `position: fixed` descendant.
	// BaseModal (and other raw `fixed inset-0` overlays) mount inside that
	// track, so pre-fix their backdrop resolved against the 400%-wide
	// transformed track instead of the viewport — small, offset, "desktop
	// mode" looking. The preset picker is a BaseModal consumer reachable from
	// Panel 0 with no generation required.
	await loginAsOwner(page);
	await page.goto('/generate');

	const panelStrip = page.getByRole('region', { name: 'Swipeable panels' });
	await expect(panelStrip).toBeVisible({ timeout: 15000 });

	await page.getByRole('button', { name: 'Preset', exact: true }).click();
	await page.waitForTimeout(400); // slide transition

	await page.locator('button[aria-haspopup="dialog"]').first().click();

	const backdrop = page.locator('div[role="button"][aria-label="Close modal"]');
	await expect(backdrop).toBeVisible();
	const backdropBox = await backdrop.boundingBox();
	expect(backdropBox, 'modal backdrop has a box').toBeTruthy();
	// Tolerance of 20px absorbs the app's `scrollbar-gutter: stable` (see the
	// first test's comment above) — a real, pre-existing few-px discrepancy
	// between the viewport and a fixed element's containing block that's
	// unrelated to this regression. The containing-block bug this guards
	// against is nowhere close to that: pre-fix, the backdrop was sized to
	// the 400%-wide panel track (~1100px too wide), not off by a few px.
	expect(Math.abs(backdropBox!.x), 'backdrop left edge at viewport origin').toBeLessThanOrEqual(20);
	expect(Math.abs(backdropBox!.width - 375), 'backdrop spans the full viewport width').toBeLessThanOrEqual(20);
});

test('a dropped mode-list request does not strand the Form panel forever', async ({ page }) => {
	// Regression: picking a preset kicks off GET /api/presets/{id}/modes to
	// auto-select a mode. That fetch is only ever retried by the fallout of
	// some OTHER reactive update (tab switch, generation event, ...) - nothing
	// re-triggers it on its own. On a flaky mobile connection a single dropped
	// request left the tab with selectedMode permanently null: the Form panel
	// stuck on "Select a mode to continue" and Panel 0's session pill stuck
	// disabled ("Session unavailable") forever, with no user-visible way to
	// recover short of reselecting the preset from scratch.
	await loginAsOwner(page);

	let modesCallCount = 0;
	await page.route('**/api/presets/*/modes', async (route) => {
		modesCallCount++;
		if (modesCallCount === 1) {
			await route.abort('failed');
			return;
		}
		await route.continue();
	});

	// installAndSelectImagePreset assumes the "Choose a preset" trigger is
	// immediately reachable, which is only true on desktop - on mobile it
	// lives in Panel 0, off-screen by default (the carousel opens on the
	// Generate panel). Install/assign via its API calls, then drive the
	// picker through the mobile chrome ourselves, same as the overlay test
	// above.
	const token = await ownerToken(page);
	const headers = { Authorization: `Bearer ${token}` };
	const list = await page.request.get('/api/presets?include_uninstalled=true', { headers });
	const presetsData = ((await list.json()).data || []) as Array<{ id: string; name: string; engine?: string; category?: string; installed?: boolean }>;
	const presetInfo =
		presetsData.find((p) => /sdxl/i.test(p.id)) || presetsData.find((p) => p.engine === 'native' && p.category === 'image');
	expect(presetInfo, 'a native image preset must exist to select').toBeTruthy();
	if (!presetInfo!.installed) {
		await page.request.post(`/api/presets/${presetInfo!.id}/install`, { headers });
	}
	const me = await page.request.get('/api/auth/me', { headers });
	const userId = (await me.json())?.data?.id;
	await page.request.post(`/api/presets/${presetInfo!.id}/assign`, { headers, data: { user_ids: [userId] } });

	await page.goto('/generate');
	const panelStrip = page.getByRole('region', { name: 'Swipeable panels' });
	await expect(panelStrip).toBeVisible({ timeout: 15000 });

	await page.getByRole('button', { name: 'Preset', exact: true }).click();
	await page.waitForTimeout(400); // slide transition
	const pickerTrigger = page.locator('button[aria-haspopup="dialog"]', { hasText: 'Choose a preset' });
	await expect(pickerTrigger).toBeVisible({ timeout: 15000 });
	await pickerTrigger.click();
	const presetList = page.locator('[role="listbox"][aria-label="Presets"]');
	await expect(presetList).toBeVisible({ timeout: 15000 });
	await presetList.getByText(presetInfo!.name, { exact: true }).click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();

	await page.getByRole('button', { name: 'Form' }).click();

	const formPanel = panelStrip.locator('.mobile-panel').nth(1);
	await expect(formPanel.getByText('Select a mode to continue')).toBeHidden({ timeout: 5000 });
	await expect(formPanel.locator('input, select, textarea').first()).toBeVisible();
	expect(modesCallCount, 'the dropped /modes request must have been retried').toBeGreaterThan(1);
});
