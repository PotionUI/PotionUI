import { test, expect, type Page, type Response } from '@playwright/test';
import { screenshot } from './helpers';
import { fixturePng } from './imageEditorHelpers';
import { prepare, api, headers } from './elixirs-fixtures';
import { openFakeStudio } from './fakeStudio';

const JOURNEY = 'cloud-fake-quick-delete-journey';
const alert = (page: Page) => page.getByRole('alertdialog');
const confirm = (page: Page) => alert(page).getByRole('button', { name: /^(Delete|Confirm)/ });

interface Exchange {
	call: string;
	status: number;
	request: string | null;
	body: string;
}

function recordTraffic(page: Page): Exchange[] {
	const seen: Exchange[] = [];
	page.on('response', async (response: Response) => {
		const url = new URL(response.url());
		if (!/^\/api\/(collections|library|generations\/history|media)/.test(url.pathname)) return;
		const method = response.request().method();
		let body = '';
		try {
			body = (await response.text()).slice(0, 1500);
		} catch {
			body = '<unreadable>';
		}
		const failed = response.status() >= 400 || /"success"\s*:\s*false/.test(body);
		if (method === 'GET' && !failed) return;
		const exchange = { call: `${method} ${url.pathname}${url.search}`, status: response.status(), request: response.request().postData(), body };
		seen.push(exchange);
		console.log(`[${JOURNEY}] ${exchange.call} -> ${exchange.status}\n  request: ${exchange.request}\n  response: ${exchange.body}`);
	});
	return seen;
}

async function leaveFocus(page: Page) {
	await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
	await expect.poll(() => page.evaluate(() => document.activeElement === document.body)).toBe(true);
}

async function select(page: Page, card: ReturnType<Page['locator']>, label: string) {
	await card.hover();
	await card.getByRole('button', { name: label }).click();
	await leaveFocus(page);
}

async function deleteEntries(page: Page, name: string) {
	const row = page.getByRole('treeitem', { name: new RegExp(name) });
	await expect(row).toBeVisible({ timeout: 15000 });
	await row.hover();
	await page.getByRole('button', { name: `${name} actions` }).click();
	await page.getByRole('menuitem', { name: 'Delete entries…' }).click();
}

async function collection(page: Page, token: string, scope: string, name: string) {
	const listed = await api(page, token, 'get', `/api/collections?scope=${scope}`);
	return (listed.data.collections as Array<{ id: string; name: string; item_count?: number }>).find((c) => c.name === name);
}

async function status(page: Page, token: string, url: string) {
	return (await page.request.get(url, { headers: headers(token) })).status();
}

async function generate(page: Page, prompt: string) {
	const editor = page.locator('[contenteditable="true"]').first();
	await editor.click();
	await page.keyboard.press('ControlOrMeta+a');
	await page.keyboard.type(prompt);
	const started = page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/generations/start');
	await page.getByRole('button', { name: 'Generate', exact: true }).click();
	const id = (await (await started).json()).data.generation_id as string;
	await expect(page.getByTestId('workbench-delete-generation')).toBeVisible({ timeout: 60000 });
	return id;
}

test.describe('quick delete against the real backend', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('Library: Delete on a selected item, then Delete entries on its collection', async ({ page }) => {
		test.setTimeout(150000);
		const traffic = recordTraffic(page);
		const { token } = await prepare(page);
		const stem = `qd-${Date.now()}`;
		const names = ['a', 'b', 'c'].map((letter) => `${stem}-${letter}.png`);
		for (const [index, name] of names.entries()) {
			const upload = await page.request.post('/api/media/upload', {
				headers: headers(token),
				multipart: { file: { name, mimeType: 'image/png', buffer: fixturePng(90 + index * 7) } }
			});
			expect(upload.ok(), `upload -> ${upload.status()} ${await upload.text()}`).toBeTruthy();
		}
		const rows = (await api(page, token, 'get', '/api/library/items?limit=200&offset=0')).data.items as Array<Record<string, any>>;
		const [a, b, c] = names.map((name) => rows.find((row) => row.original_filename === name)!);
		expect([a, b, c].every(Boolean), JSON.stringify(rows.map((row) => row.original_filename))).toBe(true);
		const fileUrl = (row: Record<string, any>) => row.url as string;
		for (const row of [a, b, c]) expect(await status(page, token, fileUrl(row)), fileUrl(row)).toBe(200);
		const collectionName = `Refs ${stem}`;
		const created = await api(page, token, 'post', '/api/collections', { name: collectionName, scope: 'library', parent_id: null });
		const collectionId = (created.data.collection?.id ?? created.data.id) as string;
		await api(page, token, 'post', `/api/collections/${collectionId}/uploads`, { upload_ids: [a.id, b.id], scope: 'library' });

		await page.goto('/library');
		const card = (row: Record<string, any>) => page.locator(`[data-library-card="${row.id}"]`);
		await expect(card(a)).toBeVisible({ timeout: 20000 });
		await select(page, card(a), 'Select item');
		await page.keyboard.press('Delete');
		await expect(alert(page)).toContainText('Delete library item', { timeout: 10000 });
		await expect(alert(page)).toContainText(names[0]);
		await screenshot(page, JOURNEY, 'library-single-1440');
		await confirm(page).click();
		await expect(card(a)).toHaveCount(0, { timeout: 15000 });
		expect(await status(page, token, `/api/library/items/${a.id}`)).toBe(404);
		expect(await status(page, token, fileUrl(a))).toBe(404);

		await deleteEntries(page, collectionName);
		await expect(alert(page)).toContainText('Delete entries?', { timeout: 10000 });
		await expect(alert(page)).toContainText('1 item');
		await expect(alert(page)).toContainText('The collection itself stays');
		await screenshot(page, JOURNEY, 'library-entries-dialog-1440');
		await confirm(page).click();
		await expect(page.getByText('Deleted 1 item.')).toBeVisible({ timeout: 15000 });
		await expect(card(b)).toHaveCount(0, { timeout: 15000 });
		await expect(card(c)).toBeVisible();
		expect(await status(page, token, fileUrl(b))).toBe(404);
		expect(await status(page, token, fileUrl(c))).toBe(200);
		const kept = await collection(page, token, 'library', collectionName);
		expect(kept?.item_count ?? -1).toBe(0);
		await expect(page.getByRole('treeitem', { name: new RegExp(collectionName) })).toBeVisible();
		await screenshot(page, JOURNEY, 'library-after-entries-1440');

		await api(page, token, 'delete', `/api/library/items/${c.id}`);
		await api(page, token, 'delete', `/api/collections/${collectionId}?scope=library`);
		expect(traffic.filter((e) => e.status >= 400 && !e.call.startsWith('GET')), JSON.stringify(traffic, null, 2)).toEqual([]);
	});

	test('History: Delete on a selected generation, then Delete entries on its collection', async ({ page }) => {
		test.setTimeout(180000);
		const traffic = recordTraffic(page);
		const { token } = await prepare(page);
		await openFakeStudio(page);
		const ids: string[] = [];
		for (const prompt of ['a red lantern', 'a green bottle', 'a blue door']) ids.push(await generate(page, prompt));
		const [first, second, third] = ids;
		const collectionName = `Keepers ${Date.now()}`;
		const created = await api(page, token, 'post', '/api/collections', { name: collectionName, scope: 'history', parent_id: null });
		const collectionId = (created.data.collection?.id ?? created.data.id) as string;
		await api(page, token, 'post', `/api/collections/${collectionId}/members`, { generation_ids: [first, second], scope: 'history' });

		await page.goto('/history');
		const card = (id: string) => page.locator(`[data-generation-card][data-generation-id="${id}"]`);
		await expect(card(first)).toBeVisible({ timeout: 20000 });
		await select(page, card(first), 'Select generation');
		await page.keyboard.press('Delete');
		await expect(alert(page)).toContainText('Delete Generation', { timeout: 10000 });
		await screenshot(page, JOURNEY, 'history-single-1440');
		await confirm(page).click();
		await expect(card(first)).toHaveCount(0, { timeout: 15000 });
		expect(await status(page, token, `/api/generations/history/${first}`)).toBe(404);

		await deleteEntries(page, collectionName);
		await expect(alert(page)).toContainText('Delete entries?', { timeout: 10000 });
		await expect(alert(page)).toContainText('1 generation');
		await screenshot(page, JOURNEY, 'history-entries-dialog-1440');
		await confirm(page).click();
		await expect(page.getByText('Deleted 1 generation.')).toBeVisible({ timeout: 15000 });
		await expect(card(second)).toHaveCount(0, { timeout: 15000 });
		await expect(card(third)).toBeVisible();
		expect(await status(page, token, `/api/generations/history/${second}`)).toBe(404);
		expect(await status(page, token, `/api/generations/history/${third}`)).toBe(200);
		const kept = await collection(page, token, 'history', collectionName);
		expect(kept?.item_count ?? -1).toBe(0);
		await screenshot(page, JOURNEY, 'history-after-entries-1440');

		await api(page, token, 'delete', `/api/generations/history/${third}`);
		await api(page, token, 'delete', `/api/collections/${collectionId}?scope=history`);
		expect(traffic.filter((e) => e.status >= 400 && !e.call.startsWith('GET')), JSON.stringify(traffic, null, 2)).toEqual([]);
	});
});
