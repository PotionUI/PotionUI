import { test, expect, type Page, type Route, type WebSocketRoute } from '@playwright/test';
import { loginAsOwner, scopedStorageKey, screenshot, addGenerationTab } from './helpers';
import { installAndSelectImagePreset } from './presetPreamble';

const JOURNEY = 'tab-drafts-sync';
const OUTBOX_KEY = 'potionui-tab-draft-outbox';
const TABS_KEY = 'potionui_tabs_state';

interface DraftOp {
	op: 'set' | 'remove';
	name: string;
	type?: string;
	value?: string;
}

interface DraftRow {
	variables: Record<string, unknown>;
	rev: number;
}

interface DraftServer {
	rows: Map<string, DraftRow>;
	posts: Array<{ tabId: string; ops: DraftOp[] }>;
	puts: Array<{ tabId: string; variables: Record<string, unknown> }>;
	deletes: string[];
	reads: string[];
	mode: { offline: boolean; limit: boolean };
}

async function mockTabDrafts(page: Page): Promise<DraftServer> {
	const server: DraftServer = {
		rows: new Map(),
		posts: [],
		puts: [],
		deletes: [],
		reads: [],
		mode: { offline: false, limit: false }
	};
	const ok = (route: Route, data: unknown) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
	const fail = (route: Route, status: number, detail: Record<string, unknown>) =>
		route.fulfill({
			status,
			contentType: 'application/json',
			body: JSON.stringify({ detail: { success: false, ...detail } })
		});
	const draftBody = (tabId: string, row: DraftRow) => ({
		tab_id: tabId,
		doc: { variables: row.variables },
		rev: row.rev,
		created_at: null,
		updated_at: null
	});

	await page.route(/\/api\/tab-drafts(\/[^?]*)?(\?.*)?$/, async (route) => {
		if (server.mode.offline) return route.abort('failed');
		const request = route.request();
		const path = new URL(request.url()).pathname.replace(/^.*\/api\/tab-drafts\/?/, '');
		const [tabId, action] = path.split('/');
		const method = request.method();

		if (!tabId && method === 'GET') {
			server.reads.push('list');
			return ok(
				route,
				[...server.rows.entries()].map(([id, row]) => ({
					tab_id: id,
					variable_count: Object.keys(row.variables).length,
					rev: row.rev,
					updated_at: null
				}))
			);
		}
		if (tabId && !action && method === 'GET') {
			server.reads.push(tabId);
			const row = server.rows.get(tabId);
			return ok(route, row ? draftBody(tabId, row) : null);
		}
		if (tabId && !action && method === 'PUT') {
			const variables = (request.postDataJSON().doc?.variables ?? {}) as Record<string, unknown>;
			server.puts.push({ tabId, variables });
			const row = { variables, rev: (server.rows.get(tabId)?.rev ?? 0) + 1 };
			server.rows.set(tabId, row);
			return ok(route, draftBody(tabId, row));
		}
		if (tabId && !action && method === 'DELETE') {
			server.deletes.push(tabId);
			return server.rows.delete(tabId)
				? ok(route, { deleted: true })
				: fail(route, 404, { error: 'tab_draft_not_found' });
		}
		if (tabId && action === 'ops' && method === 'POST') {
			if (server.mode.limit) {
				return fail(route, 409, { error: 'tab_draft_limit', message: 'Too many drafts', limit: 200 });
			}
			const ops = (request.postDataJSON().ops ?? []) as DraftOp[];
			server.posts.push({ tabId, ops });
			const row = server.rows.get(tabId) ?? { variables: {}, rev: 0 };
			for (const op of ops) {
				if (op.op === 'remove') delete row.variables[op.name];
				else row.variables[op.name] = { type: 'text', value: op.value ?? '' };
			}
			row.rev += 1;
			server.rows.set(tabId, row);
			return ok(route, draftBody(tabId, row));
		}
		return fail(route, 405, { error: 'unmocked' });
	});

	return server;
}

async function openVariables(page: Page) {
	const variablesButton = page.getByRole('button', { name: 'Variables' });
	await variablesButton.waitFor({ state: 'visible', timeout: 15000 });
	await variablesButton.click();
	const modal = page.getByRole('dialog').filter({ hasText: 'Prompt variables' });
	await expect(modal).toBeVisible({ timeout: 10000 });
	return modal;
}

async function selectPresetOrSkip(page: Page): Promise<boolean> {
	const preset = await installAndSelectImagePreset(page);
	if (!preset) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return false;
	}
	return true;
}

test('edits sync, reload rehydrates, offline outbox flushes, closing a tab deletes the draft', async ({ page }) => {
	test.setTimeout(180000);
	const server = await mockTabDrafts(page);
	await loginAsOwner(page);
	if (!(await selectPresetOrSkip(page))) return;

	let modal = await openVariables(page);
	await modal.getByLabel('Variable name').fill('style');
	await modal.getByLabel('Variable value').fill('oil painting');
	await modal.getByRole('button', { name: 'Done' }).click();

	await expect.poll(() => server.posts.length, { timeout: 10000 }).toBeGreaterThan(0);
	await expect
		.poll(() => [...server.rows.values()][0]?.variables, { timeout: 10000 })
		.toEqual({ style: { type: 'text', value: 'oil painting' } });
	const tabId = server.posts[0].tabId;
	expect(server.posts.every((post) => post.tabId === tabId)).toBe(true);

	const tabsKey = await scopedStorageKey(page, TABS_KEY);
	await expect
		.poll(async () =>
			page.evaluate((key) => {
				const raw = localStorage.getItem(key);
				return raw ? JSON.parse(raw).tabs.some((tab: Record<string, unknown>) => 'variables' in tab) : null;
			}, tabsKey)
		)
		.toBe(false);

	server.mode.offline = true;
	modal = await openVariables(page);
	await modal.getByLabel('Variable value').first().fill('watercolor');
	await modal.getByRole('button', { name: 'Done' }).click();

	const outboxKey = await scopedStorageKey(page, OUTBOX_KEY);
	await expect
		.poll(
			async () =>
				page.evaluate((key) => {
					const raw = localStorage.getItem(key);
					return raw ? Object.keys(JSON.parse(raw).ops ?? {}) : [];
				}, outboxKey),
			{ timeout: 10000 }
		)
		.toEqual([tabId]);
	expect(server.rows.get(tabId)?.variables).toEqual({ style: { type: 'text', value: 'oil painting' } });

	server.mode.offline = false;
	await page.evaluate(() => window.dispatchEvent(new Event('online')));
	await expect
		.poll(() => server.rows.get(tabId)?.variables, { timeout: 15000 })
		.toEqual({ style: { type: 'text', value: 'watercolor' } });
	await expect
		.poll(async () =>
			page.evaluate((key) => {
				const raw = localStorage.getItem(key);
				return raw ? Object.keys(JSON.parse(raw).ops ?? {}).length : 0;
			}, outboxKey)
		)
		.toBe(0);

	server.reads.length = 0;
	await page.reload();
	await expect.poll(() => server.reads, { timeout: 15000 }).toEqual(['list', tabId]);
	modal = await openVariables(page);
	await expect(modal.getByLabel('Variable value').first()).toHaveValue('watercolor');
	await modal.getByRole('button', { name: 'Done' }).click();

	await addGenerationTab(page);
	await page.locator('button.book-tab', { hasText: 'Generation 1' }).locator('.close-button').click({ force: true });
	const confirm = page.getByRole('button', { name: 'Confirm', exact: true });
	if (await confirm.isVisible().catch(() => false)) await confirm.click();
	await expect.poll(() => server.deletes, { timeout: 10000 }).toEqual([tabId]);
	expect(server.rows.has(tabId)).toBe(false);
});

test('a draft limit shows a warning toast and keeps the edit queued', async ({ page }) => {
	test.setTimeout(120000);
	const server = await mockTabDrafts(page);
	server.mode.limit = true;
	await loginAsOwner(page);
	if (!(await selectPresetOrSkip(page))) return;

	const modal = await openVariables(page);
	await modal.getByLabel('Variable name').fill('style');
	await modal.getByLabel('Variable value').fill('oil painting');
	await modal.getByRole('button', { name: 'Done' }).click();

	const toast = page.getByText(/Close old tabs/);
	await expect(toast).toBeVisible({ timeout: 15000 });
	expect(server.posts).toHaveLength(0);

	const outboxKey = await scopedStorageKey(page, OUTBOX_KEY);
	const queued = await page.evaluate((key) => {
		const raw = localStorage.getItem(key);
		return raw ? Object.values(JSON.parse(raw).ops ?? {}).length : 0;
	}, outboxKey);
	expect(queued).toBe(1);

	await page.setViewportSize({ width: 1440, height: 900 });
	await screenshot(page, JOURNEY, 'limit-toast-1440');
	await page.setViewportSize({ width: 390, height: 844 });
	await expect(toast).toBeVisible();
	await screenshot(page, JOURNEY, 'limit-toast-390');
});

test('a server push of a changed draft shows up in the open tab', async ({ page }) => {
	test.setTimeout(120000);
	const server = await mockTabDrafts(page);
	let socket: WebSocketRoute | null = null;
	await page.routeWebSocket('**/ws/notifications**', (ws) => {
		socket = ws;
		ws.send(JSON.stringify({ type: 'connection_established', unread_count: 0 }));
	});
	await loginAsOwner(page);
	if (!(await selectPresetOrSkip(page))) return;

	let modal = await openVariables(page);
	await modal.getByLabel('Variable name').fill('style');
	await modal.getByLabel('Variable value').fill('oil painting');
	await modal.getByRole('button', { name: 'Done' }).click();
	await expect.poll(() => server.posts.length, { timeout: 10000 }).toBeGreaterThan(0);
	await expect.poll(() => socket !== null, { timeout: 15000 }).toBe(true);
	const tabId = server.posts[0].tabId;
	const postsBefore = server.posts.length;

	(socket as unknown as WebSocketRoute).send(
		JSON.stringify({
			type: 'tab_variables_changed',
			tab_id: tabId,
			rev: 99,
			doc: {
				variables: {
					style: { type: 'text', value: 'oil painting' },
					mood: { type: 'text', value: 'noir' }
				}
			}
		})
	);

	await expect
		.poll(
			async () => {
				const dialog = await openVariables(page);
				const names = (await dialog.getByRole('option').allTextContents()).join(' ');
				await dialog.getByRole('button', { name: 'Done' }).click();
				await expect(dialog).toBeHidden();
				return names.includes('$mood');
			},
			{ timeout: 20000, intervals: [500, 1000] }
		)
		.toBe(true);
	await page.waitForTimeout(1500);
	expect(server.posts.length).toBe(postsBefore);
});

test('loading a saved session replaces the draft with one PUT', async ({ page }) => {
	test.setTimeout(180000);
	const server = await mockTabDrafts(page);
	await loginAsOwner(page);
	if (!(await selectPresetOrSkip(page))) return;

	const tabsKey = await scopedStorageKey(page, TABS_KEY);
	const readTabState = () =>
		page.evaluate((key) => {
			const parsed = JSON.parse(localStorage.getItem(key) || '{"tabs":[{}]}');
			const tab = parsed.tabs[0];
			return { presetId: tab.selectedPreset as string | null, mode: tab.selectedMode as string | null };
		}, tabsKey);
	await expect.poll(async () => (await readTabState()).presetId, { timeout: 15000 }).toBeTruthy();
	await expect.poll(async () => (await readTabState()).mode, { timeout: 15000 }).toBeTruthy();
	const tabState = await readTabState();
	const mode = tabState.mode || 'txt2img';
	const token = await page.evaluate(() => localStorage.getItem('auth_token'));
	const created = await page.request.post('/api/sessions/save', {
		headers: { Authorization: `Bearer ${token}` },
		data: {
			preset_id: tabState.presetId,
			name: `drafts-${Date.now()}`,
			data: {
				[mode]: {
					selectedPreset: tabState.presetId,
					selectedMode: mode,
					promptSegments: [],
					variables: {
						style: { type: 'text', value: 'oil painting' },
						mood: { type: 'text', value: 'noir' },
						pose: { type: 'text', value: 'sitting' }
					}
				}
			}
		}
	});
	expect(created.ok()).toBe(true);
	const sessionId = (await created.json()).data.id as string;

	await page.evaluate(
		({ key, id }) => {
			const parsed = JSON.parse(localStorage.getItem(key) || '{}');
			parsed.tabs[0].selectedSessionId = id;
			localStorage.setItem(key, JSON.stringify(parsed));
		},
		{ key: tabsKey, id: sessionId }
	);
	await page.reload();

	await expect.poll(() => server.puts.length, { timeout: 20000 }).toBe(1);
	expect(Object.keys(server.puts[0].variables).sort()).toEqual(['mood', 'pose', 'style']);
	await page.waitForTimeout(1500);
	expect(server.puts).toHaveLength(1);
	expect(server.posts).toHaveLength(0);
});

test('closing a tab with unsaved variables warns first and Cancel keeps the draft', async ({ page }) => {
	test.setTimeout(180000);
	const server = await mockTabDrafts(page);
	await loginAsOwner(page);
	if (!(await selectPresetOrSkip(page))) return;

	const modal = await openVariables(page);
	await modal.getByLabel('Variable name').fill('style');
	await modal.getByLabel('Variable value').fill('oil painting');
	await modal.getByRole('button', { name: 'Done' }).click();
	await expect.poll(() => server.posts.length, { timeout: 10000 }).toBeGreaterThan(0);
	const tabId = server.posts[0].tabId;

	await addGenerationTab(page);
	await page.locator('button.book-tab', { hasText: 'Generation 1' }).locator('.close-button').click({ force: true });
	const warning = page.getByText("This tab's variables aren't saved anywhere else.");
	await expect(warning).toBeVisible({ timeout: 10000 });

	await page.setViewportSize({ width: 1440, height: 900 });
	await screenshot(page, JOURNEY, 'close-warning-1440');
	await page.setViewportSize({ width: 390, height: 844 });
	await expect(warning).toBeVisible();
	await screenshot(page, JOURNEY, 'close-warning-390');
	await page.setViewportSize({ width: 1440, height: 900 });

	await page.getByRole('button', { name: 'Cancel', exact: true }).click();
	await expect(warning).toHaveCount(0);
	expect(server.deletes).toEqual([]);
	expect(server.rows.has(tabId)).toBe(true);

	await page.locator('button.book-tab', { hasText: 'Generation 1' }).locator('.close-button').click({ force: true });
	await page.getByRole('button', { name: 'Confirm', exact: true }).click();
	await expect.poll(() => server.deletes, { timeout: 10000 }).toEqual([tabId]);
});
