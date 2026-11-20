import { test, expect, type Page, type Route, type WebSocketRoute } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot, scopedStorageKey } from './helpers';
import { discoverAndAssignImagePreset } from './presetPreamble';
import { generationStatusMessage } from './generationMocks';

const JOURNEY = 'generation-attach';
const RUNNING_ID = '01E2EATTACHRUN000000000001';
const FAILING_ID = '01E2EATTACHFAIL00000000001';
const PROMPT = 'a lighthouse at dusk';
const SEED = 7771;
const KEY_NAME = 'claude desktop';
const MCP_LABEL = `Started over MCP · ${KEY_NAME}`;
const REPLAYED_PREVIEW =
	'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';
const LIVE_PREVIEW =
	'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGM4oaEBAALUARkFUI+kAAAAAElFTkSuQmCC';
const TOTAL_STEPS = 30;

interface Preset {
	id: string;
	name: string;
	mode: string;
}

interface Server {
	running: Array<Record<string, unknown>>;
	sockets: WebSocketRoute[];
	subscribes: string[];
	subscribers: Map<string, WebSocketRoute[]>;
}

const mcpOrigin = { kind: 'mcp', key_id: 'k-e2e', key_name: KEY_NAME, key_current_name: KEY_NAME };

function queueItem(id: string, preset: Preset): Record<string, unknown> {
	return {
		generation_id: id,
		backend_id: 'e2e-backend',
		preset_id: preset.id,
		tab_id: null,
		progress: null,
		status: 'running',
		preset_name: preset.name,
		mode: preset.mode,
		form_name: null,
		has_tab: false,
		created_at: new Date(Date.now() - 20000).toISOString(),
		started_at: new Date(Date.now() - 18000).toISOString(),
		origin: mcpOrigin
	};
}

function noise(preset: Preset): Array<Record<string, unknown>> {
	return [
		{ ...queueItem('01E2EATTACHEDIT0000000001', preset), mode: 'editor:edit_region', origin: { kind: 'editor', edit_id: 'e1', step_id: 's1', task: 'edit_region', run_id: 'r1' } },
		{ ...queueItem('01E2EATTACHCHAT0000000001', preset), origin: { kind: 'chat' } },
		{ ...queueItem('01E2EATTACHNONE0000000001', preset), origin: null }
	];
}

function detail(id: string, preset: Preset, status: string, extra: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		id,
		preset_id: preset.id,
		preset_name: preset.name,
		mode: preset.mode,
		form_name: null,
		form_data: { seed: SEED },
		prompt_state: { prompt: PROMPT, negativePrompt: '' },
		status,
		progress: status === 'running' ? 0.2 : 1,
		created_at: new Date(Date.now() - 20000).toISOString(),
		started_at: new Date(Date.now() - 18000).toISOString(),
		updated_at: new Date().toISOString(),
		files: [],
		rating: 0,
		is_favorite: false,
		origin: mcpOrigin,
		...extra
	};
}

async function presetWithMode(page: Page): Promise<Preset | null> {
	const found = await discoverAndAssignImagePreset(page);
	if (!found) return null;
	const token = await ownerToken(page);
	const res = await page.request.get(`/api/presets/${found.id}/modes`, { headers: { Authorization: `Bearer ${token}` } });
	const body = await res.json();
	const mode = body?.data?.default_mode || body?.data?.modes?.[0]?.name;
	return mode ? { ...found, mode } : null;
}

const ok = (route: Route, data: unknown) => route.fulfill({ json: { success: true, data } });

async function installServer(page: Page, preset: Preset, details: Record<string, Record<string, unknown>>): Promise<Server> {
	const server: Server = { running: [], sockets: [], subscribes: [], subscribers: new Map() };
	const send = (ws: WebSocketRoute, message: object) => ws.send(JSON.stringify(message));

	await page.routeWebSocket(/\/ws\/generation/, (ws) => {
		server.sockets.push(ws);
		send(ws, { type: 'connection_established', client_id: 'e2e-mock' });
		ws.onMessage((raw) => {
			const message = JSON.parse(raw as string);
			if (message.type !== 'subscribe_generation') return;
			const id = message.generation_id as string;
			server.subscribes.push(id);
			server.subscribers.set(id, [...(server.subscribers.get(id) ?? []), ws]);
			send(ws, { type: 'subscribed', generation_id: id });
			send(ws, {
				type: 'status_update',
				data: { id, status: 'running', progress: 0.7, current_step: 'Sampling <<PROGRESS:21/30>>', message: 'Sampling', created_at: new Date(Date.now() - 20000).toISOString() }
			});
			if (id === RUNNING_ID) {
				send(ws, {
					type: 'workbench_update',
					generation_id: id,
					pipe_id: 1,
					pipe_name: 'generator',
					output_type: 'image',
					temporary: true,
					seed: SEED,
					image: REPLAYED_PREVIEW,
					replayed: true
				});
			}
		});
	});

	await page.route(/\/api\/media\/generations\/01E2EATTACH[A-Z0-9]+\/.+$/, (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from(LIVE_PREVIEW.split(',')[1], 'base64') })
	);
	await page.route(/\/api\/generations\/queue(\?.*)?$/, (route) => ok(route, { pending: [], running: server.running }));
	await page.route(/\/api\/generations\/(01E2EATTACH[A-Z0-9]+)\/status(\?.*)?$/, (route) => {
		const id = route.request().url().match(/generations\/([A-Z0-9]+)\/status/)![1];
		return ok(route, { id, generation_id: id, status: 'running', progress: 0.2, created_at: new Date(Date.now() - 20000).toISOString(), started_at: new Date(Date.now() - 18000).toISOString() });
	});
	await page.route(/\/api\/generations\/history\/(01E2EATTACH[A-Z0-9]+)(\?.*)?$/, (route) => {
		const id = route.request().url().match(/history\/([A-Z0-9]+)/)![1];
		return details[id] ? ok(route, details[id]) : route.fulfill({ status: 404, json: { success: false, error: 'generation_not_found' } });
	});
	return server;
}

const send = (ws: WebSocketRoute, message: object) => ws.send(JSON.stringify(message));
const push = (server: Server, id: string, message: object) =>
	(server.subscribers.get(id) ?? []).forEach((ws) => {
		try {
			send(ws, message);
		} catch {
			return;
		}
	});
const indicator = (page: Page) => page.getByTestId('running-elsewhere');
const tabNamed = (page: Page, name: string) => page.locator('.book-tab', { hasText: name });

async function openFromIndicator(page: Page, id: string) {
	await indicator(page).click();
	const row = page.locator(`[data-elsewhere-row="${id}"]`);
	await expect(row).toBeVisible();
	await row.getByRole('button', { name: 'Open in a tab' }).click();
}

async function persistedTabs(page: Page): Promise<Array<Record<string, unknown>>> {
	const key = await scopedStorageKey(page, 'potionui_tabs_state');
	return page.evaluate((storageKey) => {
		const raw = localStorage.getItem(storageKey);
		return raw ? (JSON.parse(raw).tabs ?? []) : [];
	}, key);
}

test.describe('a generation started outside a tab shows up and can be followed', () => {
	let preset: Preset;

	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await loginAsOwner(page);
		const found = await presetWithMode(page);
		if (!found) {
			test.skip(true, 'No native image preset with a mode on this throwaway instance.');
			return;
		}
		preset = found;
	});

	test('an MCP generation running with no tab: indicator, Open in a tab, settings, replayed preview, live progress, result', async ({ page }) => {
		test.setTimeout(120000);
		const server = await installServer(page, preset, { [RUNNING_ID]: detail(RUNNING_ID, preset, 'running') });
		server.running = [queueItem(RUNNING_ID, preset), ...noise(preset)];

		await page.goto('/generate');
		await expect(indicator(page)).toHaveText('1 running over MCP', { timeout: 20000 });
		await screenshot(page, JOURNEY, 'indicator-1440');

		await indicator(page).click();
		const row = page.locator(`[data-elsewhere-row="${RUNNING_ID}"]`);
		await expect(row).toContainText(preset.name);
		await expect(row).toContainText(MCP_LABEL);
		await expect(page.locator('[data-elsewhere-row]')).toHaveCount(1);
		await expect(row.locator('[data-elsewhere-state]')).toHaveText('Running');
		await expect(row.locator('[data-elsewhere-elapsed]')).toBeVisible();
		await screenshot(page, JOURNEY, 'popover-1440');

		const before = await tabNamed(page, '').count();
		await row.getByRole('button', { name: 'Open in a tab' }).click();

		await expect(tabNamed(page, preset.name).first()).toBeVisible();
		await expect(page.locator('.book-tab.active [data-testid="tab-source"]')).toBeVisible();
		await expect(indicator(page)).toHaveCount(0);
		expect(await tabNamed(page, '').count()).toBe(before + 1);

		await expect
			.poll(async () => (await persistedTabs(page)).find((tab) => (tab.openedFrom as { generationId?: string } | null)?.generationId === RUNNING_ID))
			.toMatchObject({ selectedPreset: preset.id, selectedMode: preset.mode, prompt: PROMPT, activeGenerationId: RUNNING_ID, formData: { seed: SEED } });

		const preview = page.locator(`img[src="${REPLAYED_PREVIEW}"]`).first();
		await expect(preview).toBeVisible({ timeout: 15000 });
		await expect(page.getByRole('progressbar', { name: 'Generation progress' })).toHaveAttribute('aria-valuenow', '70', { timeout: 10000 });

		const push_ = (message: object) => push(server, RUNNING_ID, message);
		push_(
			generationStatusMessage({ generationId: RUNNING_ID, pipeId: 1, pipeName: 'generator', currentStep: 'Sampling <<PROGRESS:15/30>>', progress: 0.5, stepNum: 15, totalSteps: TOTAL_STEPS })
		);
		const progressBar = page.getByRole('progressbar', { name: 'Generation progress' });
		await expect(progressBar).toHaveAttribute('aria-valuenow', '50', { timeout: 10000 });

		push_({ type: 'workbench_update', generation_id: RUNNING_ID, pipe_id: 1, pipe_name: 'generator', output_type: 'image', temporary: true, image: LIVE_PREVIEW });
		await expect(page.locator(`img[src="${LIVE_PREVIEW}"]`).first()).toBeVisible({ timeout: 10000 });
		push_({ type: 'workbench_update', generation_id: RUNNING_ID, pipe_id: 1, pipe_name: 'generator', output_type: 'image', temporary: true, image: REPLAYED_PREVIEW, replayed: true });
		await page.waitForTimeout(500);
		await expect(page.locator(`img[src="${LIVE_PREVIEW}"]`).first()).toBeVisible();
		await expect(page.locator(`img[src="${REPLAYED_PREVIEW}"]`)).toHaveCount(0);
		await screenshot(page, JOURNEY, 'following-1440');

		push_({
			type: 'gallery_update',
			generation_id: RUNNING_ID,
			pipe_id: 2,
			pipe_name: 'gallery',
			output_type: 'gallery',
			images: [LIVE_PREVIEW, LIVE_PREVIEW],
			image_urls_list: [
				{ derived: false, seed: SEED },
				{ derived: false, seed: SEED + 1 }
			]
		});
		push_({ type: 'generation_complete', data: { id: RUNNING_ID, generation_id: RUNNING_ID, status: 'completed', progress: 1, created_at: new Date().toISOString(), completed_at: new Date().toISOString() } });
		await expect(page.getByAltText(/Generated image/).first()).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, 'completed-1440');
	});

	test('a failing one shows the reason and its hint', async ({ page }) => {
		test.setTimeout(90000);
		const server = await installServer(page, preset, { [FAILING_ID]: detail(FAILING_ID, preset, 'running') });
		server.running = [queueItem(FAILING_ID, preset)];

		await page.goto('/generate');
		await expect(indicator(page)).toHaveText('1 running over MCP', { timeout: 20000 });
		await openFromIndicator(page, FAILING_ID);
		await expect(tabNamed(page, preset.name).first()).toBeVisible();
		await expect.poll(() => server.subscribes.includes(FAILING_ID), { timeout: 10000 }).toBe(true);

		push(server, FAILING_ID, {
			type: 'generation_error',
			generation_id: FAILING_ID,
			message: 'This was too much for the GPU right now.',
			hint: 'Try a smaller size.',
			error_id: 'E2E-ERR-1',
			error_code: 'cuda_oom'
		});
		const empty = page.getByTestId('workbench-empty');
		await expect(empty).toContainText('Generation failed', { timeout: 10000 });
		await expect(empty).toContainText('This was too much for the GPU right now.');
		await expect(empty).toContainText('Try a smaller size.');
		await screenshot(page, JOURNEY, 'failed-1440');
	});

	test('opening the same generation twice focuses the same tab, and a reload follows it again', async ({ page }) => {
		test.setTimeout(150000);
		const server = await installServer(page, preset, { [RUNNING_ID]: detail(RUNNING_ID, preset, 'running') });
		server.running = [queueItem(RUNNING_ID, preset)];
		await page.route(/\/api\/generations\/history(\?.*)?$/, (route) =>
			ok(route, { generations: [detail(RUNNING_ID, preset, 'running')], total: 1, limit: 24, offset: 0 })
		);

		await page.goto('/generate');
		await expect(indicator(page)).toHaveText('1 running over MCP', { timeout: 20000 });
		await openFromIndicator(page, RUNNING_ID);
		await expect(page.locator('.book-tab.active [data-testid="tab-source"]')).toBeVisible();
		const tabCount = await tabNamed(page, '').count();

		await page.goto('/history');
		const card = page.locator('[data-generation-card]').first();
		await expect(card).toBeVisible({ timeout: 20000 });
		await expect(card.locator('[data-origin-chip="mcp"]')).toContainText(MCP_LABEL);
		await card.getByRole('button', { name: 'Item actions' }).click();
		await page.locator('[data-tools-menu] [data-tool="open-in-tab"]').click();
		await expect(page).toHaveURL(/\/generate/);
		await expect(page.locator('.book-tab.active [data-testid="tab-source"]')).toBeVisible();
		expect(await tabNamed(page, '').count()).toBe(tabCount);

		const subscribesBefore = server.subscribes.filter((id) => id === RUNNING_ID).length;
		await page.reload();
		await expect(page.locator('.book-tab [data-testid="tab-source"]')).toBeVisible({ timeout: 20000 });
		await expect.poll(() => server.subscribes.filter((id) => id === RUNNING_ID).length, { timeout: 20000 }).toBeGreaterThan(subscribesBefore);
		await expect(indicator(page)).toHaveCount(0);

		await expect(page.getByRole('progressbar', { name: 'Generation progress' })).toHaveAttribute('aria-valuenow', '70', { timeout: 10000 });
	});

	test('a generation that is not there leaves no tab behind', async ({ page }) => {
		const server = await installServer(page, preset, {});
		server.running = [queueItem(RUNNING_ID, preset)];
		await page.goto('/generate');
		await expect(indicator(page)).toHaveText('1 running over MCP', { timeout: 20000 });
		const before = await tabNamed(page, '').count();
		await openFromIndicator(page, RUNNING_ID);
		await expect(page.getByText('This generation is no longer available.')).toBeVisible();
		expect(await tabNamed(page, '').count()).toBe(before);
	});

	for (const size of [
		{ tag: '1440', width: 1440, height: 900 },
		{ tag: '390', width: 390, height: 844 }
	]) {
		test(`History names where each generation was started at ${size.tag}`, async ({ page }) => {
			test.setTimeout(90000);
			await page.setViewportSize({ width: size.width, height: size.height });
			const items = [
				detail('01E2EATTACHHIST00000000001', preset, 'failed', { error_message: 'boom' }),
				detail('01E2EATTACHHIST00000000002', preset, 'failed', { error_message: 'boom', origin: { kind: 'chat' } }),
				detail('01E2EATTACHHIST00000000003', preset, 'failed', { error_message: 'boom', origin: null })
			];
			await page.route(/\/api\/generations\/history(\?.*)?$/, (route) => ok(route, { generations: items, total: items.length, limit: 24, offset: 0 }));
			await page.goto('/history');
			const cards = page.locator('[data-generation-card]');
			await expect(cards).toHaveCount(3, { timeout: 20000 });
			const named = async (card: ReturnType<typeof cards.nth>, kind: string, label: string) => {
				const chip = card.locator(`[data-origin-chip="${kind}"]`);
				if (size.width >= 768) await expect(chip).toContainText(label);
				else await expect(chip).toHaveAttribute('aria-label', label);
			};
			await named(cards.nth(0), 'mcp', MCP_LABEL);
			await named(cards.nth(1), 'chat', 'Started from chat');
			await expect(cards.nth(2).locator('[data-origin-chip]')).toHaveCount(0);
			await screenshot(page, JOURNEY, `history-chips-${size.tag}`);

			await cards.nth(1).getByRole('button', { name: 'Item actions' }).click({ force: true });
			await expect(page.locator('[data-tools-menu] [data-tool="open-details"]')).toBeVisible();
			await expect(page.locator('[data-tools-menu] [data-tool="open-in-tab"]')).toHaveCount(0);
			await page.keyboard.press('Escape');

			await cards.nth(0).getByRole('button', { name: 'Item actions' }).click({ force: true });
			await page.locator('[data-tools-menu] [data-tool="open-details"]').click();
			const origin = page.locator('[data-details-origin]');
			await expect(origin).toContainText(MCP_LABEL, { timeout: 10000 });
			await expect(origin.locator('button, a')).toHaveCount(0);
			await screenshot(page, JOURNEY, `details-chip-${size.tag}`);
		});
	}
});

test.describe('on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

	test('the running elsewhere list opens in a sheet and opens the generation in a tab', async ({ page }) => {
		test.setTimeout(120000);
		await loginAsOwner(page);
		const preset = await presetWithMode(page);
		if (!preset) {
			test.skip(true, 'No native image preset with a mode on this throwaway instance.');
			return;
		}
		const server = await installServer(page, preset, { [RUNNING_ID]: detail(RUNNING_ID, preset, 'running') });
		server.running = [queueItem(RUNNING_ID, preset)];

		await page.goto('/generate');
		await expect(page.locator('.studio-dock')).toBeVisible({ timeout: 20000 });
		const button = page.getByRole('button', { name: '1 running over MCP' });
		await expect(button).toBeVisible({ timeout: 20000 });
		await screenshot(page, JOURNEY, 'indicator-390');

		await button.click();
		const sheet = page.getByRole('dialog', { name: 'Started over MCP' });
		await expect(sheet).toBeVisible();
		await expect(sheet).toContainText(MCP_LABEL);
		await screenshot(page, JOURNEY, 'sheet-390');

		await sheet.getByRole('button', { name: 'Open in a tab' }).click();
		await expect(sheet).toHaveCount(0);
		await expect(page.getByRole('button', { name: /^Open tabs/ })).toContainText(preset.name);
		await expect(page.locator(`img[src="${REPLAYED_PREVIEW}"]`).first()).toBeVisible({ timeout: 15000 });
		await screenshot(page, JOURNEY, 'following-390');
	});
});
