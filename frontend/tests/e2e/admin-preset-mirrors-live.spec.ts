import { test, expect, type Page, type Response } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'admin-preset-mirrors-live';
const RENAMED = 'Live Rename Check';
const MIRROR_NAME = 'Live Mirror Check';
const PREFIX = 'kodachrome film still,';
const SUFFIX = 'warm grain';

interface ListedPreset {
	id: string;
	name: string;
	installed?: boolean;
	modes?: string[];
	is_mirror?: boolean;
	engine?: string | null;
}

interface Exchange {
	method: string;
	url: string;
	status: number;
	request: string | null;
	body: string;
}

const exchanges: Exchange[] = [];
const state: { token: string; sourceId: string; mirrorId: string; installedHere: boolean } = {
	token: '',
	sourceId: '',
	mirrorId: '',
	installedHere: false
};

function recordPresetTraffic(page: Page) {
	page.on('response', async (response: Response) => {
		const url = new URL(response.url());
		if (!url.pathname.startsWith('/api/presets')) return;
		const request = response.request();
		let body = '';
		try {
			body = await response.text();
		} catch {
			body = '<unreadable>';
		}
		const failed = response.status() >= 400 || /"success"\s*:\s*false/.test(body);
		if (request.method() === 'GET' && !failed) return;
		const exchange = {
			method: request.method(),
			url: `${url.pathname}${url.search}`,
			status: response.status(),
			request: request.postData(),
			body: body.slice(0, 2000)
		};
		exchanges.push(exchange);
		console.log(`[${JOURNEY}] ${exchange.method} ${exchange.url} -> ${exchange.status}\n  request: ${exchange.request}\n  response: ${exchange.body}`);
	});
}

async function api(page: Page, method: 'get' | 'post' | 'put', url: string, data?: unknown) {
	const response = await page.request[method](url, {
		headers: { Authorization: `Bearer ${state.token}` },
		...(data === undefined ? {} : { data })
	});
	return { status: response.status(), body: await response.json().catch(() => null) };
}

async function openAdmin(page: Page, query = '') {
	await page.goto(`/admin?tab=presets${query}`);
}

function catalogCard(page: Page, name: string) {
	return page
		.locator('[role="list"][aria-label="Preset catalog"]')
		.locator('[data-preset-card]')
		.filter({ has: page.locator(`text="${name}"`) });
}

async function moreActions(page: Page, item: string) {
	await page.getByRole('button', { name: 'More actions' }).click();
	await page.getByRole('menuitem', { name: item }).click();
}

test.afterEach(async ({ page }) => {
	if (!state.token) return;
	if (state.mirrorId) await api(page, 'post', `/api/presets/${state.mirrorId}/uninstall`);
	if (!state.sourceId) return;
	if (state.installedHere) {
		await api(page, 'post', `/api/presets/${state.sourceId}/uninstall`);
	} else {
		await api(page, 'put', `/api/presets/${state.sourceId}/display`, { name: null, description: null, cover: null });
	}
	console.log(`[${JOURNEY}] disagreements or refusals seen: ${exchanges.filter((e) => e.status >= 400).length}`);
});

test('an admin renames a preset, mirrors it with fixed prompt text, and removes both against the real backend', async ({ page }) => {
	test.setTimeout(180000);
	await page.setViewportSize({ width: 1440, height: 900 });
	recordPresetTraffic(page);
	await loginAsOwner(page);
	state.token = await ownerToken(page);

	await test.step('pick a file preset with at least two modes and install it if needed', async () => {
		const listed = await api(page, 'get', '/api/presets?include_uninstalled=true');
		expect(listed.status).toBe(200);
		const presets = (listed.body?.data ?? []) as ListedPreset[];
		const candidates = presets.filter((p) => !p.is_mirror && (p.modes?.length ?? 0) >= 2);
		const chosen = candidates.find((p) => p.installed) ?? candidates.find((p) => /z-?image/i.test(p.id)) ?? candidates[0];
		if (!chosen) {
			test.skip(true, 'No preset with two or more modes on this throwaway instance.');
			return;
		}
		state.sourceId = chosen.id;
		if (!chosen.installed) {
			const installed = await api(page, 'post', `/api/presets/${chosen.id}/install`);
			expect(installed.status, JSON.stringify(installed.body)).toBe(200);
			state.installedHere = true;
		}
		console.log(`[${JOURNEY}] source=${chosen.id} name=${chosen.name} installedHere=${state.installedHere}`);
	});

	await test.step('open the preset from the library and rename it on the Display tab', async () => {
		await openAdmin(page);
		const listedName = (await api(page, 'get', '/api/presets?include_uninstalled=true')).body.data.find(
			(p: ListedPreset) => p.id === state.sourceId
		).name as string;
		const card = catalogCard(page, listedName);
		await expect(card).toBeVisible({ timeout: 20000 });
		await card.getByRole('button', { name: 'Open' }).click();
		await expect(page).toHaveURL(new RegExp(`id=${state.sourceId}`), { timeout: 15000 });
		await openAdmin(page, `&id=${state.sourceId}&view=display`);
		const name = page.locator('#preset-display-name');
		await expect(name).toBeVisible({ timeout: 20000 });
		await name.fill(RENAMED);
		await expect(page.getByText('1 unsaved change')).toBeVisible();
		await page.getByRole('button', { name: 'Save', exact: true }).click();
		await expect(page.getByText('1 unsaved change')).toHaveCount(0, { timeout: 15000 });
		await expect(page.locator('[data-detail-title]')).toHaveText(RENAMED, { timeout: 15000 });
		await screenshot(page, JOURNEY, 'source-renamed-1440');
	});

	await test.step('the library card shows the new name', async () => {
		await openAdmin(page);
		await expect(catalogCard(page, RENAMED)).toBeVisible({ timeout: 20000 });
		await screenshot(page, JOURNEY, 'library-renamed-1440');
	});

	await test.step('create a mirror offering one mode and land on it', async () => {
		await openAdmin(page, `&id=${state.sourceId}`);
		await expect(page.locator('[data-detail-title]')).toHaveText(RENAMED, { timeout: 20000 });
		await moreActions(page, 'Create mirror');
		const dialog = page.getByRole('dialog');
		await expect(dialog.locator('[data-create-mirror-form]')).toBeVisible({ timeout: 15000 });
		await dialog.locator('#create-mirror-name').fill(MIRROR_NAME);
		const labels = await dialog.locator('#create-mirror-first-mode option').allTextContents();
		expect(labels.length).toBeGreaterThanOrEqual(2);
		for (const label of labels.slice(1)) {
			await dialog.getByRole('button', { name: label.trim(), exact: true }).click();
		}
		await expect(dialog.locator('#create-mirror-first-mode option')).toHaveCount(1);
		await screenshot(page, JOURNEY, 'create-mirror-1440');
		await dialog.getByRole('button', { name: 'Create mirror', exact: true }).click();
		await expect(page).not.toHaveURL(new RegExp(`id=${state.sourceId}(&|$)`), { timeout: 15000 });
		const id = new URL(page.url()).searchParams.get('id');
		expect(id).toBeTruthy();
		state.mirrorId = id as string;
		await expect(page.locator('[data-detail-title]')).toHaveText(MIRROR_NAME, { timeout: 15000 });
		await expect(page.locator('[data-detail-subtitle]')).toContainText('Mirror of');
		await screenshot(page, JOURNEY, 'mirror-landed-1440');
	});

	await test.step('set the mirror’s fixed prompt text and save it', async () => {
		await openAdmin(page, `&id=${state.mirrorId}&view=prompt`);
		const prefix = page.locator('[data-fixed-prompt-input="prefix"]');
		await expect(prefix).toBeVisible({ timeout: 20000 });
		await prefix.fill(PREFIX);
		await page.locator('[data-fixed-prompt-input="suffix"]').fill(SUFFIX);
		await page.getByRole('button', { name: 'Save', exact: true }).click();
		await expect(page.getByText(/unsaved change/)).toHaveCount(0, { timeout: 15000 });
		await screenshot(page, JOURNEY, 'mirror-prompt-text-saved-1440');
	});

	await test.step('after a reload the mirror keeps its name, its single mode and its prompt text', async () => {
		await page.reload();
		await expect(page.locator('[data-detail-title]')).toHaveText(MIRROR_NAME, { timeout: 20000 });
		await expect(page.locator('[data-fixed-prompt-input="prefix"]')).toHaveValue(PREFIX, { timeout: 20000 });
		await expect(page.locator('[data-fixed-prompt-input="suffix"]')).toHaveValue(SUFFIX);
		await openAdmin(page, `&id=${state.mirrorId}&view=display`);
		await expect(page.locator('[data-mirror-modes]')).toBeVisible({ timeout: 20000 });
		await expect(page.locator('#mirror-first-mode option')).toHaveCount(1);
		await expect(page.locator('#preset-display-name')).toHaveValue(MIRROR_NAME);
		await expect(page.locator('[data-mirror-source-link]')).toHaveText(RENAMED);
		await screenshot(page, JOURNEY, 'mirror-after-reload-1440');
	});

	await test.step('the library marks the mirror as a mirror of the renamed source', async () => {
		await openAdmin(page);
		const mirrorCard = catalogCard(page, MIRROR_NAME);
		await expect(mirrorCard).toBeVisible({ timeout: 20000 });
		await expect(mirrorCard.locator('[data-mirror-source]')).toHaveText(`Mirror of ${RENAMED}`);
		await expect(catalogCard(page, RENAMED).locator('[data-preset-mirror-count]')).toBeVisible();
		await screenshot(page, JOURNEY, 'library-with-mirror-1440');
	});

	await test.step('removing the source is refused and the dialog names the mirror', async () => {
		await openAdmin(page, `&id=${state.sourceId}`);
		await expect(page.locator('[data-detail-title]')).toHaveText(RENAMED, { timeout: 20000 });
		await moreActions(page, 'Uninstall');
		await page.getByRole('alertdialog').getByRole('button', { name: 'Uninstall' }).click();
		const blocked = page.locator('[data-preset-has-mirrors]');
		await expect(blocked).toBeVisible({ timeout: 15000 });
		await expect(blocked).toContainText('Remove its mirrors first');
		await expect(blocked.locator('[data-has-mirrors-item]')).toHaveCount(1);
		await expect(blocked.locator('[data-has-mirrors-item]')).toContainText(MIRROR_NAME);
		await screenshot(page, JOURNEY, 'source-has-mirrors-1440');
		await blocked.locator('[data-has-mirrors-item]').getByRole('button', { name: 'Open' }).click();
		await expect(page).toHaveURL(new RegExp(`id=${state.mirrorId}`), { timeout: 15000 });
	});

	await test.step('remove the mirror', async () => {
		await expect(page.locator('[data-detail-title]')).toHaveText(MIRROR_NAME, { timeout: 20000 });
		await moreActions(page, 'Remove mirror');
		await page.getByRole('alertdialog').getByRole('button', { name: 'Remove', exact: true }).click();
		await expect(page.getByText(`${MIRROR_NAME} removed`)).toBeVisible({ timeout: 15000 });
		const listed = await api(page, 'get', '/api/presets?include_uninstalled=true');
		expect((listed.body.data as ListedPreset[]).some((p) => p.id === state.mirrorId)).toBe(false);
		state.mirrorId = '';
		await screenshot(page, JOURNEY, 'mirror-removed-1440');
	});

	await test.step('leave the instance as it was: uninstall the source or restore its name', async () => {
		if (state.installedHere) {
			await openAdmin(page, `&id=${state.sourceId}`);
			await expect(page.locator('[data-detail-title]')).toHaveText(RENAMED, { timeout: 20000 });
			await moreActions(page, 'Uninstall');
			await page.getByRole('alertdialog').getByRole('button', { name: 'Uninstall' }).click();
			await expect(page.getByText(`${RENAMED} uninstalled`)).toBeVisible({ timeout: 15000 });
			const listed = await api(page, 'get', '/api/presets?include_uninstalled=true');
			const source = (listed.body.data as ListedPreset[]).find((p) => p.id === state.sourceId);
			expect(source?.installed).toBe(false);
			expect(source?.name).not.toBe(RENAMED);
		} else {
			await openAdmin(page, `&id=${state.sourceId}&view=display`);
			const name = page.locator('#preset-display-name');
			await expect(name).toHaveValue(RENAMED, { timeout: 20000 });
			await name.fill('');
			await page.getByRole('button', { name: 'Save', exact: true }).click();
			await expect(page.locator('[data-detail-title]')).not.toHaveText(RENAMED, { timeout: 15000 });
		}
		state.sourceId = '';
		await screenshot(page, JOURNEY, 'source-restored-1440');
	});

	const refused = exchanges.filter((e) => e.status >= 400 && !(e.status === 409 && e.url.endsWith('/uninstall')));
	expect(refused, JSON.stringify(refused, null, 2)).toEqual([]);
});
