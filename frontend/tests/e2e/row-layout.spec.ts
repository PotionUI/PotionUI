import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot, shotPath } from './helpers';

// A "row" field group (e.g. Seed + Images) used to only render side-by-side
// once the settings pane was dragged well past its default width - the
// ResizeObserver-measured collapse boundary (480px, later drifted to an
// unwired 640px in rowLayout.ts) sat above the pane's real default content
// width (~348px = 380px leftPanelWidth - 32px DynamicForm padding).
//
// The fix removed the drag entirely: the settings pane is now a fixed width
// per viewport tier (380/420/460px, see generationLayout.ts's
// settingsPaneWidth), and the row-collapse boundary (rowLayout.ts's default
// 300px) sits below all three tiers' content width (348/388/428px) on
// purpose, so a default 2-column row like Seed + Images never folds at any
// reachable desktop viewport. This walk replaces the old drag-to-narrow
// interaction with `page.setViewportSize` across the desktop tiers -
// including the narrowest one, right above the mobile cutoff (768px) -
// asserting the row keeps sharing one line at all of them.

const JOURNEY = 'row-layout';
const BEAT = 300;

const DESKTOP_VIEWPORTS = [
	{ label: 'narrow-desktop', width: 800, height: 900 },
	{ label: 'xl-desktop', width: 1300, height: 900 },
	{ label: '2xl-desktop', width: 1600, height: 900 }
] as const;

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

test('row layout — Seed and Images share one line at every desktop viewport tier', async ({ page }) => {
	await page.setViewportSize({ width: DESKTOP_VIEWPORTS[0].width, height: DESKTOP_VIEWPORTS[0].height });
	await loginAsOwner(page);
	const token = await ownerToken(page);

	const me = await apiGet(page, '/api/auth/me', token);
	const userId = me.data.id as string;

	const list = await apiGet(page, '/api/presets?include_uninstalled=true', token);
	const presets = (list.data || []) as Array<{
		id: string;
		name: string;
		engine?: string;
		category?: string;
		installed?: boolean;
	}>;
	const preset = presets.find((p) => /sdxl/i.test(p.name)) || presets.find((p) => /flux/i.test(p.name));

	if (!preset) {
		test.skip(true, 'No SDXL or Flux preset available on this throwaway instance.');
		return;
	}

	if (!preset.installed) {
		await apiPost(page, `/api/presets/${preset.id}/install`, token);
	}
	await apiPost(page, `/api/presets/${preset.id}/assign`, token, { user_ids: [userId] });

	await page.goto('/generate');
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByText(preset.name, { exact: true }).first().click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();

	const generationTab = page.getByRole('tab', { name: 'Generation' });
	if ((await generationTab.count()) > 0) {
		await generationTab.click();
	}
	await page.waitForTimeout(BEAT);

	const seedLabel = page.locator('label', { hasText: 'Seed' }).first();
	const imagesLabel = page.locator('label', { hasText: 'Images' }).first();
	await expect(seedLabel).toBeVisible({ timeout: 20000 });
	await expect(imagesLabel).toBeVisible();

	const rowGrid = page.locator('.row-grid').filter({ has: page.locator('label', { hasText: 'Seed' }) });
	await expect(rowGrid).toBeVisible();

	const sameLineTolerance = 4;

	for (const viewport of DESKTOP_VIEWPORTS) {
		await page.setViewportSize({ width: viewport.width, height: viewport.height });
		await page.waitForTimeout(BEAT);
		await rowGrid.scrollIntoViewIfNeeded();

		await screenshot(page, JOURNEY, `01-${viewport.label}-full-page`);
		await rowGrid.screenshot({ path: shotPath(JOURNEY, `02-${viewport.label}-row`) });

		const seedBox = await seedLabel.boundingBox();
		const imagesBox = await imagesLabel.boundingBox();
		expect(seedBox, `seed label bounding box at ${viewport.label}`).not.toBeNull();
		expect(imagesBox, `images label bounding box at ${viewport.label}`).not.toBeNull();

		const gap = Math.abs(seedBox!.y - imagesBox!.y);
		expect(
			gap,
			`Seed and Images should share one line at ${viewport.label} (${viewport.width}px)`
		).toBeLessThanOrEqual(sameLineTolerance);

		console.log(`[${JOURNEY}] preset="${preset.name}" viewport=${viewport.label} gap=${gap.toFixed(1)}`);
	}
});
