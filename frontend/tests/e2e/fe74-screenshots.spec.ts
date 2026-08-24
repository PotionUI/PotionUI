import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot, shotPath } from './helpers';

// Visual capture only — NO functional changes are made by this spec. It
// seeds a realistic multi-segment prompt (named/colored sections, a break,
// negative segments) into the reworked "one panel" SegmentedPromptEditor and
// screenshots every context the coordinator asked about: the editor-first
// single-segment default, a multi-section prompt with the negative footer
// both expanded and collapsed, a hover state, light + dark theme, and a
// compact context (/prompts workspace + the video-director inspector).

const JOURNEY = 'fe74-screenshots';

async function apiGet(page: Page, url: string, token: string) {
	const res = await page.request.get(url, { headers: { Authorization: `Bearer ${token}` } });
	expect(res.ok(), `GET ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

async function apiPost(page: Page, url: string, token: string, data?: unknown) {
	const res = await page.request.post(url, {
		headers: { Authorization: `Bearer ${token}` },
		data: data ?? {}
	});
	expect(res.ok(), `POST ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

async function ensureAssigned(page: Page, token: string, userId: string, preset: { id: string; installed?: boolean }) {
	if (!preset.installed) {
		await apiPost(page, `/api/presets/${preset.id}/install`, token);
	}
	await apiPost(page, `/api/presets/${preset.id}/assign`, token, { user_ids: [userId] });
}

async function typeIntoSegment(page: Page, listAriaLabel: string, index: number, text: string) {
	const item = page.locator(`div[role="list"][aria-label="${listAriaLabel}"] [role="listitem"]`).nth(index);
	const editor = item.locator('.inline-chip-editor[role="textbox"]');
	await editor.click();
	await page.keyboard.type(text);
}

async function setSegmentMeta(page: Page, listAriaLabel: string, index: number, name: string, colorSwatch: string) {
	const item = page.locator(`div[role="list"][aria-label="${listAriaLabel}"] [role="listitem"]`).nth(index);
	await item.hover();
	// bbff1e9f moved "Edit details" out of the "More actions" menu into its own
	// always-visible footer button ("Details") — the menu is reserved for
	// move/replace/convert/delete now that the footer covers the rest.
	const detailsBtn = item.getByRole('button', { name: 'Details' });
	await detailsBtn.click();
	await item.getByPlaceholder('Optional segment name').fill(name);
	// bbff1e9f also replaced the free-text hex input with a fixed swatch
	// palette (PRESET_COLORS) — pick by the swatch's accessible name.
	await item.getByRole('button', { name: colorSwatch, exact: true }).click();
	// Toggle the metadata reveal back closed via the same footer button.
	await detailsBtn.click();
}

test('one-panel prompt editor — visual capture only', async ({ page }) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);

	const me = await apiGet(page, '/api/auth/me', token);
	const userId = me.data.id as string;

	const list = await apiGet(page, '/api/presets?include_uninstalled=true', token);
	const presets = (list.data || []) as Array<{ id: string; name: string; engine?: string; category?: string; installed?: boolean }>;

	const imagePreset =
		presets.find((p) => /sdxl/i.test(p.id)) ||
		presets.find((p) => p.engine === 'native' && p.category === 'image');
	const videoPreset =
		presets.find((p) => /wan/i.test(p.id)) ||
		presets.find((p) => /ltx/i.test(p.id)) ||
		presets.find((p) => p.engine === 'native' && p.category === 'video');

	console.log(`[${JOURNEY}] presets available=${presets.length} image=${imagePreset?.id} video=${videoPreset?.id}`);

	if (!imagePreset) {
		test.skip(true, 'No native image preset available on this throwaway instance to render the prompt editor with.');
		return;
	}

	await ensureAssigned(page, token, userId, imagePreset);
	if (videoPreset) await ensureAssigned(page, token, userId, videoPreset);

	const consoleErrors: string[] = [];
	const pageErrors: string[] = [];
	page.on('console', (msg) => {
		if (msg.type() === 'error') consoleErrors.push(msg.text());
	});
	page.on('pageerror', (err) => pageErrors.push(String(err)));

	// ---------------------------------------------------------------------
	// /generate — wide viewport (1440px)
	// ---------------------------------------------------------------------
	await page.setViewportSize({ width: 1440, height: 960 });
	await page.goto('/generate');
	await page.waitForLoadState('networkidle');
	await page.waitForTimeout(1000);

	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByRole('listbox', { name: 'Presets' }).getByText(imagePreset.name, { exact: true }).click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();

	const mainList = page.locator('div[role="list"][aria-label="Positive segments"]');
	await expect(mainList).toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(500);

	// -------------------------------------------------------------------
	// Editor-first default: exactly one blank, unnamed, uncolored, enabled
	// section — one consistent view, so its rule (#1) renders like any other
	// segment's would. The negative footer is likewise empty, so it reads
	// "— none".
	// -------------------------------------------------------------------
	await expect(mainList.locator('[role="listitem"]')).toHaveCount(1);
	// `.section-rule` (with a "#1" label) was replaced by a plain zero-padded
	// `.index` span on the card head in bbff1e9f (no "#" sigil).
	await expect(mainList.locator('.index')).toHaveCount(1);
	await expect(mainList.locator('.index')).toContainText('01');
	await screenshot(page, JOURNEY, '00-generate-single-segment-default');

	// Menu reachability on the lone segment — "Replace from saved" etc. are
	// available from the rule's own hover cluster, same as any other segment.
	const singleSegment = mainList.locator('[role="listitem"]').first();
	await singleSegment.hover();
	await singleSegment.locator('button[aria-haspopup="menu"]').click();
	await page.waitForTimeout(200);
	await expect(page.getByRole('menuitem', { name: 'Replace from saved' })).toBeVisible();
	await page.keyboard.press('Escape');
	await page.waitForTimeout(200);

	// Seed 3 named/colored positive sections + 1 break.
	// "Section" was renamed "Add segment" in bbff1e9f, and both the positive
	// and negative add-rows now share that exact label — .first() still lands
	// on the positive one since it precedes the negative row in DOM order.
	const sectionBtn = page.getByRole('button', { name: 'Add segment' }).first();
	await sectionBtn.click(); // now 2 segments
	await sectionBtn.click(); // now 3 segments

	await setSegmentMeta(page, 'Positive segments', 0, 'SUBJECT', 'Orange');
	await typeIntoSegment(
		page,
		'Positive segments',
		0,
		'A weathered lighthouse keeper standing on a rain-slicked stone balcony, wool coat buttoned to the chin, one hand steadying a brass telescope.'
	);

	await setSegmentMeta(page, 'Positive segments', 1, 'SCENE', 'Green');
	await typeIntoSegment(
		page,
		'Positive segments',
		1,
		'A remote clifftop lighthouse at dusk, jagged rocks below, storm clouds rolling in from the horizon, distant fishing boats fighting the swell.'
	);

	await setSegmentMeta(page, 'Positive segments', 2, 'LIGHTING', 'Blue');
	await typeIntoSegment(
		page,
		'Positive segments',
		2,
		'Cold blue-grey ambient light from the storm, warm amber glow spilling from the lighthouse lamp room, dramatic rim light on the keeper\'s silhouette.'
	);

	// Add a 4th section and convert it into a typographic break.
	await sectionBtn.click();
	const breakItem = page.locator('div[role="list"][aria-label="Positive segments"] [role="listitem"]').nth(3);
	await breakItem.hover();
	await breakItem.locator('button[aria-haspopup="menu"]').click();
	await breakItem.getByRole('menuitem', { name: 'Convert to break' }).click();

	// Negative footer starts expanded (mode supports negatives, nothing
	// configured yet) — seed the existing blank segment + add one more.
	const negativeList = page.locator('div[role="list"][aria-label="Negative segments"]');
	await expect(negativeList).toBeVisible({ timeout: 10000 });
	await typeIntoSegment(page, 'Negative segments', 0, 'blurry, low detail, extra fingers, watermark, oversaturated');
	// The negative region's own "Add segment" row — both add-rows share the
	// exact label since bbff1e9f, so the negative one is the second in DOM order.
	await page.getByRole('button', { name: 'Add segment' }).nth(1).click();
	await typeIntoSegment(page, 'Negative segments', 1, 'flat lighting, cropped composition, jpeg artifacts');

	await page.waitForTimeout(300);
	await screenshot(page, JOURNEY, '01-generate-wide-negative-expanded');

	// bbff1e9f removed the negative region's collapse toggle — it repeats the
	// prompt's anatomy exactly and is always expanded, so there is no
	// collapsed state left to capture here.

	// Header close-up crop. `.panel-header` was renamed `.section-header`.
	const header = page.locator('.section-header').first();
	await header.screenshot({ path: shotPath(JOURNEY, '03-header-closeup') });

	// Hover state over a section card — the footer strip is always visible now
	// (bbff1e9f), so this captures whatever else hover adds (e.g. drag cursor)
	// rather than revealing the controls themselves.
	const firstRule = mainList.locator('.section-wrapper').first();
	await firstRule.hover();
	await page.waitForTimeout(200);
	await screenshot(page, JOURNEY, '04-generate-hover-section');

	// Metadata inline editor open via the always-visible "Details" footer button.
	const firstRuleDetailsBtn = firstRule.getByRole('button', { name: 'Details' });
	await firstRuleDetailsBtn.click();
	await page.waitForTimeout(200);
	await screenshot(page, JOURNEY, '05-generate-metadata-editor-open');
	// Close it back down before continuing.
	await firstRuleDetailsBtn.click();

	// Action menu of the LAST negative segment, at the bottom of the panel.
	// The panel wrapper clips with overflow-hidden, so this segment's menu
	// used to render only "Move up" before the clipped-off items — assert
	// "Delete" is fully visible in the viewport now that the menu repositions
	// itself with position: fixed instead of relying on absolute layout.
	const lastNegativeItem = negativeList.locator('[role="listitem"]').last();
	await lastNegativeItem.hover();
	await lastNegativeItem.locator('button[aria-haspopup="menu"]').click();
	await page.waitForTimeout(200);
	await screenshot(page, JOURNEY, '12-last-segment-menu-open');
	const lastSegmentDelete = page.getByRole('menuitem', { name: 'Delete' });
	await expect(lastSegmentDelete).toBeVisible();
	const deleteBox = await lastSegmentDelete.boundingBox();
	expect(deleteBox, 'Delete menu item should have a bounding box').not.toBeNull();
	if (deleteBox) {
		const viewport = page.viewportSize();
		expect(viewport).not.toBeNull();
		if (viewport) {
			expect(deleteBox.y).toBeGreaterThanOrEqual(0);
			expect(deleteBox.y + deleteBox.height).toBeLessThanOrEqual(viewport.height);
		}
	}
	// Close the menu before continuing.
	await page.keyboard.press('Escape');

	// ---------------------------------------------------------------------
	// /generate — narrower viewport (~900px)
	// ---------------------------------------------------------------------
	await page.setViewportSize({ width: 900, height: 900 });
	await page.waitForTimeout(300);
	await screenshot(page, JOURNEY, '06-generate-narrow-900');

	// ---------------------------------------------------------------------
	// /generate — light theme (dev override), wide viewport
	// ---------------------------------------------------------------------
	await page.setViewportSize({ width: 1440, height: 960 });
	// Flip the theme attribute in place (no reload) so the seeded segments
	// survive — everything reads `rgb(var(--token))` off this attribute, per
	// src/lib/stores/theme.ts `apply()`.
	await page.evaluate(() => {
		document.documentElement.dataset.theme = 'light';
		const meta = document.querySelector('meta[name="theme-color"]');
		if (meta) meta.setAttribute('content', '#F7F7F7');
	});
	await page.waitForTimeout(300);
	await screenshot(page, JOURNEY, '07-generate-light-theme');

	await page.evaluate(() => {
		document.documentElement.dataset.theme = 'dark';
		const meta = document.querySelector('meta[name="theme-color"]');
		if (meta) meta.setAttribute('content', '#0D0D0D');
	});
	await page.waitForTimeout(300);
	await screenshot(page, JOURNEY, '07b-generate-dark-theme');

	// ---------------------------------------------------------------------
	// /prompts — Prompt workspace (compact context)
	// ---------------------------------------------------------------------
	await page.goto('/prompts');
	await page.waitForLoadState('networkidle');
	await page.waitForTimeout(500);
	await page.getByRole('button', { name: 'New prompt' }).click();
	await page.waitForTimeout(300);
	// This instance passes a custom `label` ("Prompt composition"), not the
	// default "Positive segments" — target the contenteditable directly.
	const compactEditor = page.locator('.inline-chip-editor[role="textbox"]').first();
	if (await compactEditor.count() > 0) {
		await compactEditor.click();
		await page.keyboard.type('A compact reusable composition, saved to the prompt library.');
	}
	await page.waitForTimeout(300);
	await screenshot(page, JOURNEY, '08-prompts-workspace-compact');

	// ---------------------------------------------------------------------
	// /prompts — Segment Templates workspace (compact context)
	// ---------------------------------------------------------------------
	await page.getByRole('button', { name: 'Segment Templates' }).click();
	await page.waitForTimeout(500);
	const newTemplateBtn = page.getByRole('button', { name: 'New Template' });
	if (await newTemplateBtn.count() > 0) {
		await newTemplateBtn.click();
		await page.waitForTimeout(300);
		const templateEditor = page.locator('.inline-chip-editor[role="textbox"]').first();
		if (await templateEditor.count() > 0) {
			await templateEditor.click();
			await page.keyboard.type('Reusable template slot content.');
			await page.waitForTimeout(300);
		}
	}
	await screenshot(page, JOURNEY, '09-prompts-segment-templates-compact');

	// ---------------------------------------------------------------------
	// Video Director inspector (compact context) — only if a video preset
	// with declared video_director capabilities is available.
	// ---------------------------------------------------------------------
	if (videoPreset) {
		await page.goto('/generate');
		await page.waitForLoadState('networkidle');
		await page.waitForTimeout(500);
		// The picker trigger shows the current preset's name once one is
		// selected, so match on the button's role rather than its label.
		const pickerTrigger = page.locator('button[aria-haspopup="dialog"]').first();
		await pickerTrigger.click();
		const pickerVisible = await page.getByRole('listbox', { name: 'Presets' }).isVisible().catch(() => false);
		if (pickerVisible) {
			const entry = page.getByRole('listbox', { name: 'Presets' }).getByText(videoPreset.name, { exact: true });
			if (await entry.count() > 0) {
				await entry.click();
				await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
				await page.waitForTimeout(1500);
				// Try to land on a "video" mode if a segmented mode selector exposes
				// one — exact match only: the preset-picker trigger's accessible
				// name also contains "video" (its engine/category subtitle line),
				// so a substring match grabs that button instead and reopens the
				// picker modal.
				const videoModeBtn = page.getByRole('button', { name: 'Video', exact: true });
				if (await videoModeBtn.count() > 0) {
					await videoModeBtn.click();
					await page.waitForTimeout(1000);
				}
				const pickerStillOpen = await page.getByRole('listbox', { name: 'Presets' }).isVisible().catch(() => false);
				if (pickerStillOpen) {
					console.log(`[${JOURNEY}] preset picker unexpectedly reopened before the director shot — closing it`);
					await page.keyboard.press('Escape');
					await page.waitForTimeout(300);
				}
				await screenshot(page, JOURNEY, '10-video-director-inspector');

				// The modeless Stage & Rail editor has no separate "Director"
				// sub-mode to switch into — the rail already shows the shot
				// sequence inline. "+" the rail's add-shot control (if reachable)
				// to capture a multi-shot rail at the compact scale, same intent
				// as the old chained-shots shot.
				const addShotButton = page.getByRole('button', { name: 'Add shot' });
				if (await addShotButton.count() > 0 && (await addShotButton.isEnabled().catch(() => false))) {
					await addShotButton.click();
					await page.waitForTimeout(500);
				}
				await page.mouse.wheel(0, 400);
				await page.waitForTimeout(200);
				await screenshot(page, JOURNEY, '11-video-director-chained-shots');
			} else {
				console.log(`[${JOURNEY}] video preset "${videoPreset.name}" not found in picker list — skipping director shot`);
			}
		} else {
			console.log(`[${JOURNEY}] preset picker did not open — skipping director shot`);
		}
	} else {
		console.log(`[${JOURNEY}] no video preset with video_director capability available — skipping director shot`);
	}

	console.log(`[${JOURNEY}] console errors (${consoleErrors.length}): ${JSON.stringify(consoleErrors.slice(0, 20))}`);
	console.log(`[${JOURNEY}] page errors (${pageErrors.length}): ${JSON.stringify(pageErrors.slice(0, 20))}`);
});
