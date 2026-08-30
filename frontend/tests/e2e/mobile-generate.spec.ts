import { test, expect } from '@playwright/test';
import { loginAsOwner, screenshot, ownerToken } from './helpers';

const JOURNEY = 'mobile-generate';

// Phone-viewport shell contract for the generate page's Studio shell (the
// camera-app posture: full-bleed canvas, a floating top bar, a floating dock
// with the prompt pill + shutter, and every heavy control — prompt, settings,
// preset/session, chat — living in a bottom sheet over the canvas). Guards
// the class of regression where the page overflows behind the fixed bottom
// tab bar (100vh shells inside a pb-reserved main), the dock ends up
// underneath the tab bar instead of anchored above it, or a sheet's nested
// overlay (the preset picker) resolves its `position: fixed` against the
// wrong containing block instead of the viewport.
test.use({ viewport: { width: 375, height: 812 }, hasTouch: true });

test('generate page fits and shows the Studio shell at phone width', async ({ page }) => {
	await loginAsOwner(page);
	await page.goto('/generate');

	// Mobile chrome is up: full-bleed canvas + floating top bar + dock, and the
	// bottom tab bar, no desktop sidebar.
	const canvas = page.locator('.studio-canvas');
	await expect(canvas).toBeVisible({ timeout: 15000 });
	await expect(page.locator('.studio-topbar')).toBeVisible();
	await expect(page.locator('.studio-dock')).toBeVisible();
	const tabBar = page.locator('nav.fixed.bottom-0');
	await expect(tabBar.filter({ hasText: 'Generate' })).toBeVisible();

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

	// The canvas is full-bleed: it fills the content area left-to-right, flush
	// with the viewport's left edge (the regression this replaces was panels
	// sized in vw instead of container %, drifting out of alignment).
	const canvasBox = await canvas.boundingBox();
	expect(canvasBox, 'canvas has a box').toBeTruthy();
	expect(Math.abs(canvasBox!.x), 'canvas flush with viewport left edge').toBeLessThanOrEqual(1);
	expect(Math.abs(canvasBox!.width - 375), 'canvas spans the full viewport width').toBeLessThanOrEqual(1);

	// Top bar: tab pill + preset/mode pill + AI chat trigger.
	await expect(page.getByRole('button', { name: 'Open tabs' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Open preset and session' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Open AI chat' })).toBeVisible();

	// Dock: prompt pill + shutter, anchored ABOVE the fixed bottom tab bar, not
	// underneath or overlapping it.
	const promptPill = page.getByRole('button', { name: 'Prompt' });
	const shutter = page.getByRole('button', { name: 'Generate', exact: true });
	await expect(promptPill).toBeVisible();
	await expect(shutter).toBeVisible();
	const shutterBox = await shutter.boundingBox();
	const tabBarBox = await tabBar.boundingBox();
	expect(shutterBox!.y + shutterBox!.height, 'shutter sits above the tab bar').toBeLessThanOrEqual(tabBarBox!.y + 1);

	// The bottom tab bar must sit fully inside the viewport.
	expect(tabBarBox!.y + tabBarBox!.height, 'tab bar bottom edge on-screen').toBeLessThanOrEqual(812 + 1);
	expect(tabBarBox!.y, 'tab bar visible above viewport bottom').toBeGreaterThan(700);

	await screenshot(page, JOURNEY, 'studio-idle');
});

test('prompt pill opens the prompt sheet; Escape closes it', async ({ page }) => {
	await loginAsOwner(page);
	await page.goto('/generate');

	await expect(page.locator('.studio-dock')).toBeVisible({ timeout: 15000 });
	await page.getByRole('button', { name: 'Prompt' }).click();

	const promptSheet = page.getByRole('dialog', { name: 'Prompt' });
	await expect(promptSheet).toBeVisible({ timeout: 5000 });
	// The default (segmented) prompt editor — reachable with no preset chosen,
	// since PromptSection falls back to it whenever no other mode (relay,
	// director, multi-prompt) is active.
	await expect(promptSheet.getByRole('list', { name: 'Positive segments' })).toBeVisible();
	await screenshot(page, JOURNEY, 'prompt-sheet');

	await page.keyboard.press('Escape');
	// The sheet is destroyed on close (not just hidden) — its document-level
	// Escape listener must go with it, or a second Escape press elsewhere in
	// the app would hit a detached instance.
	await expect(promptSheet).toHaveCount(0);
});

test('Settings pill opens the settings sheet; backdrop click closes it', async ({ page }) => {
	await loginAsOwner(page);
	await page.goto('/generate');

	await expect(page.locator('.studio-dock')).toBeVisible({ timeout: 15000 });
	await page.getByRole('button', { name: 'Settings', exact: true }).click();

	const settingsSheet = page.getByRole('dialog', { name: 'Settings' });
	await expect(settingsSheet).toBeVisible({ timeout: 5000 });
	await screenshot(page, JOURNEY, 'settings-sheet');

	// Click the backdrop, not the sheet panel — the sheet is anchored to the
	// bottom (max-height 88%), leaving the top of the viewport as backdrop.
	await page.mouse.click(20, 20);
	await expect(settingsSheet).toHaveCount(0);
});

test('overlay opened from inside a Studio sheet covers the full viewport', async ({ page }) => {
	// Regression this replaces: the old mobile carousel's swipe track carried a
	// translateX transform, which becomes the containing block for any
	// `position: fixed` descendant — a raw `fixed inset-0` overlay opened from
	// inside a panel resolved against the 400%-wide transformed track instead
	// of the viewport (small, offset, "desktop mode" looking). Studio's sheets
	// are portaled to <body> instead, but the guard is still worth keeping: a
	// modal opened from INSIDE a sheet (portal-in-portal) must still size
	// itself against the viewport. The preset picker is a BaseModal consumer
	// reachable from the preset & session sheet with no generation required.
	await loginAsOwner(page);
	await page.goto('/generate');

	await expect(page.locator('.studio-dock')).toBeVisible({ timeout: 15000 });
	await page.getByRole('button', { name: 'Open preset and session' }).click();
	const presetSheet = page.getByRole('dialog', { name: 'Preset and session' });
	await expect(presetSheet).toBeVisible({ timeout: 5000 });

	await presetSheet.locator('button[aria-haspopup="dialog"]').first().click();

	const backdrop = page.locator('div[role="button"][aria-label="Close modal"]');
	await expect(backdrop).toBeVisible();
	const backdropBox = await backdrop.boundingBox();
	expect(backdropBox, 'modal backdrop has a box').toBeTruthy();
	// Tolerance of 20px absorbs the app's `scrollbar-gutter: stable` — a real,
	// pre-existing few-px discrepancy between the viewport and a fixed
	// element's containing block that's unrelated to this regression. The
	// containing-block bug this guards against is nowhere close to that:
	// pre-fix, the backdrop was sized to the 400%-wide panel track (~1100px
	// too wide), not off by a few px.
	expect(Math.abs(backdropBox!.x), 'backdrop left edge at viewport origin').toBeLessThanOrEqual(20);
	expect(Math.abs(backdropBox!.width - 375), 'backdrop spans the full viewport width').toBeLessThanOrEqual(20);
});

test('a dropped mode-list request does not strand the settings sheet forever', async ({ page }) => {
	// Regression: picking a preset kicks off GET /api/presets/{id}/modes to
	// auto-select a mode. That fetch is only ever retried by the fallout of
	// some OTHER reactive update (tab switch, generation event, ...) - nothing
	// re-triggers it on its own. On a flaky mobile connection a single dropped
	// request left the tab with selectedMode permanently null: the settings
	// sheet stuck with no form fields (GenerationFormPane never mounts without
	// a selected mode) and Panel 0's session pill stuck disabled ("Session
	// unavailable") forever, with no user-visible way to recover short of
	// reselecting the preset from scratch.
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
	// lives inside the preset & session sheet, closed by default. Install/
	// assign via its API calls, then drive the picker through the mobile
	// chrome ourselves, same as the overlay test above.
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
	await expect(page.locator('.studio-dock')).toBeVisible({ timeout: 15000 });

	await page.getByRole('button', { name: 'Open preset and session' }).click();
	const presetSheet = page.getByRole('dialog', { name: 'Preset and session' });
	await expect(presetSheet).toBeVisible({ timeout: 5000 });

	const pickerTrigger = presetSheet.locator('button[aria-haspopup="dialog"]', { hasText: 'Choose a preset' });
	await expect(pickerTrigger).toBeVisible({ timeout: 15000 });
	await pickerTrigger.click();
	const presetList = page.locator('[role="listbox"][aria-label="Presets"]');
	await expect(presetList).toBeVisible({ timeout: 15000 });
	await presetList.getByText(presetInfo!.name, { exact: true }).click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();

	await page.keyboard.press('Escape');
	await expect(presetSheet).toHaveCount(0);

	await page.getByRole('button', { name: 'Settings', exact: true }).click();
	const settingsSheet = page.getByRole('dialog', { name: 'Settings' });
	await expect(settingsSheet).toBeVisible({ timeout: 5000 });

	// A form field actually renders — proof the retried /modes request landed
	// and selectedMode got set, not just that the sheet opened.
	await expect(settingsSheet.locator('input, select, textarea').first()).toBeVisible({ timeout: 10000 });
	expect(modesCallCount, 'the dropped /modes request must have been retried').toBeGreaterThan(1);
});
