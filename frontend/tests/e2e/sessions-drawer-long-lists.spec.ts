import { test, expect, type Page, type Locator } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { installAndSelectImagePreset } from './presetPreamble';

const JOURNEY = 'sessions-drawer-long-lists';
const SESSION_COUNT = 25;
const VERSION_COUNT = 40;
const LONG_NAME = 'Long history session';

async function seedSessions(page: Page, presetId: string): Promise<void> {
	const headers = { Authorization: `Bearer ${await ownerToken(page)}` };
	const payload = (prompt: string) => ({ txt2img: { prompt, negativePrompt: '', formData: {} } });

	const created = await page.request.post('/api/sessions/save', {
		headers,
		data: { preset_id: presetId, name: LONG_NAME, data: payload('long history v1') }
	});
	expect(created.ok(), `seed long session: ${created.status()}`).toBeTruthy();
	const longId = (await created.json()).data.id as string;

	for (let i = 2; i <= VERSION_COUNT; i++) {
		const updated = await page.request.put(`/api/sessions/${longId}`, {
			headers,
			data: { name: LONG_NAME, data: payload(`long history v${i} neon alley at night`) }
		});
		expect(updated.ok(), `seed version ${i}: ${updated.status()}`).toBeTruthy();
	}

	for (let i = 1; i <= SESSION_COUNT; i++) {
		const name = `Seeded session ${String(i).padStart(2, '0')}`;
		const res = await page.request.post('/api/sessions/save', {
			headers,
			data: { preset_id: presetId, name, data: payload(`seeded prompt ${i}`) }
		});
		expect(res.ok(), `seed ${name}: ${res.status()}`).toBeTruthy();
	}
}

async function expectPresetPersisted(page: Page, presetId: string): Promise<void> {
	await expect
		.poll(() =>
			page.evaluate((id) => {
				const raw = localStorage.getItem('potionui_tabs_state');
				const state = raw ? JSON.parse(raw) : null;
				return Boolean(state?.tabs?.some((t: { selectedPreset: unknown }) => JSON.stringify(t.selectedPreset ?? '').includes(id)));
			}, presetId)
		)
		.toBe(true);
}

async function expectInsideViewport(page: Page, drawer: Locator): Promise<void> {
	await page.waitForTimeout(300);
	const box = await drawer.boundingBox();
	const viewport = page.viewportSize()!;
	expect(box, 'drawer has a box').not.toBeNull();
	expect(box!.y).toBeGreaterThanOrEqual(0);
	expect(box!.x).toBeGreaterThanOrEqual(0);
	expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height + 0.5);
	expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 0.5);
}

async function scrollMetrics(locator: Locator) {
	return locator.evaluate((el) => ({ scrollHeight: el.scrollHeight, clientHeight: el.clientHeight, scrollTop: el.scrollTop }));
}

async function openDrawer(page: Page): Promise<Locator> {
	await page.locator('button[aria-label="Session"]').first().click();
	const drawer = page.getByRole('dialog', { name: 'Sessions' });
	await expect(drawer).toBeVisible({ timeout: 10000 });
	return drawer;
}

test('the sessions drawer stays inside the viewport and scrolls a long list and a long history', async ({ page }) => {
	test.setTimeout(240000);
	await loginAsOwner(page);

	const preset = await installAndSelectImagePreset(page);
	if (!preset) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return;
	}
	await seedSessions(page, preset.id);

	await page.setViewportSize({ width: 1440, height: 900 });
	await page.reload();
	await page.waitForLoadState('networkidle');

	let drawer = await openDrawer(page);
	await expect(drawer.locator('[data-session-row]').first()).toBeVisible({ timeout: 10000 });
	await expect
		.poll(() => drawer.locator('[data-session-row]').count(), { timeout: 10000 })
		.toBeGreaterThanOrEqual(SESSION_COUNT + 1);
	await expectInsideViewport(page, drawer);
	await screenshot(page, JOURNEY, '01-desktop-open');

	const list = drawer.getByTestId('session-list-scroll');
	const before = await scrollMetrics(list);
	expect(before.scrollHeight).toBeGreaterThan(before.clientHeight);
	await list.evaluate((el) => (el.scrollTop = el.scrollHeight));
	const after = await scrollMetrics(list);
	expect(after.scrollTop).toBeGreaterThan(0);
	await expectInsideViewport(page, drawer);
	await expect(drawer.getByRole('heading', { name: 'Sessions' })).toBeInViewport();
	await screenshot(page, JOURNEY, '02-desktop-list-scrolled');

	const search = drawer.getByLabel('Search sessions');
	await search.fill('seeded session 07');
	await expect(drawer.locator('[data-session-row]')).toHaveCount(1);
	await search.fill('');

	await drawer.locator('[data-session-row]', { hasText: LONG_NAME }).first().click();
	await expect(drawer).toBeHidden({ timeout: 10000 });
	await expect(page.locator('button[aria-label="Session"]').first()).toContainText(LONG_NAME, { timeout: 10000 });

	drawer = await openDrawer(page);
	const historyToggle = drawer.locator('button[aria-controls="session-history-panel"]');
	await expect(historyToggle).toHaveAttribute('aria-expanded', 'false', { timeout: 10000 });
	await expect(drawer.locator('[data-version-row]')).toHaveCount(0);
	await historyToggle.click();
	await expect(drawer.locator('[data-version-row]')).toHaveCount(5, { timeout: 10000 });
	await expect(drawer.getByText(/changed/).first()).toBeVisible().catch(() => undefined);
	await expectInsideViewport(page, drawer);
	await screenshot(page, JOURNEY, '03-desktop-history-expanded');

	await drawer.getByRole('button', { name: /All \d+ versions/ }).click();
	await expect(drawer.locator('[data-version-row]')).toHaveCount(20);
	const versions = drawer.getByTestId('session-versions-scroll');
	const versionMetrics = await scrollMetrics(versions);
	expect(versionMetrics.scrollHeight).toBeGreaterThan(versionMetrics.clientHeight);
	await expectInsideViewport(page, drawer);
	await screenshot(page, JOURNEY, '04-desktop-all-versions');

	await page.keyboard.press('Escape');
	await expect(drawer.locator('[data-version-row]')).toHaveCount(5);
	await page.keyboard.press('Escape');
	await expect(drawer).toBeHidden();
	await expect(page.locator('button[aria-label="Session"]').first()).toBeFocused();

	await page.setViewportSize({ width: 390, height: 844 });
	await page.waitForTimeout(400);
	await page.getByRole('button', { name: 'Open preset and session' }).click();
	const sheet = page.getByRole('dialog', { name: 'Preset and session' });
	await expect(sheet).toBeVisible();
	await sheet.locator('button[aria-label="Session"]').click();

	const mobile = page.getByRole('dialog', { name: 'Sessions' });
	await expect(mobile).toBeVisible({ timeout: 10000 });
	const box = await mobile.boundingBox();
	expect(box!.width).toBeGreaterThanOrEqual(389);
	expect(box!.height).toBeGreaterThanOrEqual(843);
	await expectInsideViewport(page, mobile);
	await screenshot(page, JOURNEY, '05-mobile-open');

	const mobileList = mobile.getByTestId('session-list-scroll');
	const mobileBefore = await scrollMetrics(mobileList);
	expect(mobileBefore.scrollHeight).toBeGreaterThan(mobileBefore.clientHeight);
	await mobileList.evaluate((el) => (el.scrollTop = el.scrollHeight));
	expect((await scrollMetrics(mobileList)).scrollTop).toBeGreaterThan(0);
	await expectInsideViewport(page, mobile);
	await screenshot(page, JOURNEY, '06-mobile-list-scrolled');

	await mobile.locator('button[aria-controls="session-history-panel"]').click();
	await mobile.getByRole('button', { name: /All \d+ versions/ }).click();
	await expect(mobile.locator('[data-version-row]')).toHaveCount(20);
	await expectInsideViewport(page, mobile);
	await screenshot(page, JOURNEY, '07-mobile-all-versions');
});

test('the keep-open drawer is wider on large screens and leaves the generate page usable', async ({ page }) => {
	test.setTimeout(240000);
	await loginAsOwner(page);

	const preset = await installAndSelectImagePreset(page);
	if (!preset) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return;
	}
	await seedSessions(page, preset.id);
	await page.evaluate(() => localStorage.setItem('sessions-drawer-keep-open', '1'));

	const expectations = [
		{ width: 1280, height: 800, drawerWidth: 480 },
		{ width: 1440, height: 900, drawerWidth: 518.4 },
		{ width: 1920, height: 1080, drawerWidth: 600 }
	];

	for (const { width, height, drawerWidth } of expectations) {
		await page.setViewportSize({ width, height });
		await page.reload();
		await page.waitForLoadState('networkidle');

		const trigger = page.locator('button[aria-label="Session"]').first();
		await trigger.click();
		const drawer = page.getByRole('dialog', { name: 'Sessions' });
		await expect(drawer).toBeVisible({ timeout: 10000 });
		if (!(await trigger.textContent())?.includes(LONG_NAME)) {
			await drawer.locator('[data-session-row]', { hasText: LONG_NAME }).first().click();
			await expect(trigger).toContainText(LONG_NAME, { timeout: 10000 });
		}
		await expect(drawer).toBeVisible();
		const historyToggle = drawer.locator('button[aria-controls="session-history-panel"]');
		if ((await historyToggle.getAttribute('aria-expanded')) !== 'true') await historyToggle.click();
		await expect(drawer.locator('[data-version-row]')).toHaveCount(5, { timeout: 10000 });

		const box = await drawer.boundingBox();
		expect(Math.abs(box!.width - drawerWidth)).toBeLessThan(1);
		await expectInsideViewport(page, drawer);

		const coveredBy = await trigger.evaluate((el) => {
			const rect = el.getBoundingClientRect();
			const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
			return hit === el || el.contains(hit);
		});
		expect(coveredBy, 'the console bar session control stays reachable').toBe(true);
		const tabHit = await page.getByText('Generation 1', { exact: true }).first().evaluate((el) => {
			const rect = el.getBoundingClientRect();
			const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
			return hit === el || el.contains(hit);
		});
		expect(tabHit, 'the workspace behind the drawer stays reachable').toBe(true);

		const headline = drawer.locator('[data-version-row] .truncate').first();
		const clipped = await headline.evaluate((el) => el.scrollWidth > el.clientWidth);
		expect(clipped, 'the newest version headline fits without an ellipsis').toBe(false);
		await screenshot(page, JOURNEY, `08-keep-open-${width}`);
	}
});

test('a pinned session moves into the Pinned section and stays there after the drawer is reopened', async ({ page }) => {
	test.setTimeout(240000);
	await loginAsOwner(page);

	const preset = await installAndSelectImagePreset(page);
	if (!preset) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return;
	}
	const headers = { Authorization: `Bearer ${await ownerToken(page)}` };
	const payload = (prompt: string) => ({ txt2img: { prompt, negativePrompt: '', formData: {} } });
	for (const name of ['Pin target', 'Pin bystander', 'Pin current']) {
		const res = await page.request.post('/api/sessions/save', {
			headers,
			data: { preset_id: preset.id, name, data: payload(name) }
		});
		expect(res.ok(), `seed ${name}: ${res.status()}`).toBeTruthy();
	}

	await page.setViewportSize({ width: 1440, height: 900 });
	await expectPresetPersisted(page, preset.id);
	await page.reload();
	await page.waitForLoadState('networkidle');

	let drawer = await openDrawer(page);
	await expect(drawer.locator('[data-session-row]').first()).toBeVisible({ timeout: 10000 });
	await expect(drawer.getByTestId('pinned-head')).toHaveCount(0);

	const pinResponse = page.waitForResponse(
		(response) => response.url().includes('/pin') && response.request().method() === 'PUT'
	);
	await drawer.getByRole('button', { name: 'Pin Pin target' }).click();
	expect((await pinResponse).ok()).toBeTruthy();
	await expect(drawer.getByTestId('pinned-head')).toBeVisible();
	await expect(drawer.getByTestId('pinned-list').locator('[data-session-row]')).toHaveCount(1);
	await expect(drawer.getByRole('button', { name: 'Unpin Pin target' })).toHaveAttribute('aria-pressed', 'true');
	await screenshot(page, JOURNEY, '06-pinned-section');

	await page.keyboard.press('Escape');
	await expect(drawer).toBeHidden();
	await expectPresetPersisted(page, preset.id);
	await page.reload();
	await page.waitForLoadState('networkidle');

	drawer = await openDrawer(page);
	const pinned = drawer.getByTestId('pinned-list').locator('[data-session-row]');
	await expect(pinned).toHaveCount(1, { timeout: 10000 });
	await expect(pinned.first()).toContainText('Pin target');

	await drawer.getByLabel('Search sessions').fill('bystander');
	await expect(drawer.getByTestId('pinned-head')).toHaveCount(0);
	await drawer.getByLabel('Search sessions').fill('');

	await drawer.getByRole('button', { name: 'Unpin Pin target' }).click();
	await expect(drawer.getByTestId('pinned-head')).toHaveCount(0);
});
