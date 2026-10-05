import { test, expect, type Page, type Locator } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { deflateSync } from 'node:zlib';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'qwen21-extract-only-guide';
const PRESET_ID = '01M31Y6WNM07EHAXXCDV1QVJVX';
const TABS_STORAGE_KEY = 'potionui_tabs_state';
const LABEL = 'Guide: Canny';

function crc32(buf: Buffer): number {
	let c = ~0;
	for (const b of buf) {
		c ^= b;
		for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
	}
	return ~c >>> 0;
}

function chunk(type: string, data: Buffer): Buffer {
	const head = Buffer.alloc(4);
	head.writeUInt32BE(data.length);
	const body = Buffer.concat([Buffer.from(type), data]);
	const tail = Buffer.alloc(4);
	tail.writeUInt32BE(crc32(body));
	return Buffer.concat([head, body, tail]);
}

function edgeMapPng(size: number): Buffer {
	const rows: Buffer[] = [];
	const c = size / 2;
	for (let y = 0; y < size; y++) {
		const row = Buffer.alloc(1 + size * 3);
		for (let x = 0; x < size; x++) {
			const r = Math.hypot(x - c, y - c);
			const edge =
				Math.abs(r - size * 0.3) < 1.2 ||
				Math.abs(r - size * 0.18) < 1.2 ||
				(Math.abs(x - y) < 1.2 && r < size * 0.3) ||
				(y === Math.round(size * 0.82) && x > size * 0.15 && x < size * 0.85);
			const v = edge ? 255 : 0;
			row[1 + x * 3] = v;
			row[2 + x * 3] = v;
			row[3 + x * 3] = v;
		}
		rows.push(row);
	}
	const ihdr = Buffer.alloc(13);
	ihdr.writeUInt32BE(size, 0);
	ihdr.writeUInt32BE(size, 4);
	ihdr[8] = 8;
	ihdr[9] = 2;
	return Buffer.concat([
		Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
		chunk('IHDR', ihdr),
		chunk('IDAT', deflateSync(Buffer.concat(rows))),
		chunk('IEND', Buffer.alloc(0))
	]);
}

async function prepare(page: Page, headers: Record<string, string>): Promise<string> {
	const list = await page.request.get('/api/presets?include_uninstalled=true', { headers });
	const presets = ((await list.json()).data || []) as Array<{ id: string; name: string; installed?: boolean }>;
	const preset = presets.find((p) => p.id === PRESET_ID);
	test.skip(!preset, 'Qwen-Image-2.1 preset is not available on this instance');
	if (!preset!.installed) {
		const res = await page.request.post(`/api/presets/${PRESET_ID}/install`, { headers });
		expect(res.ok(), `install -> ${res.status()}`).toBeTruthy();
	}
	const me = await page.request.get('/api/auth/me', { headers });
	const userId = (await me.json()).data.id as string;
	const assign = await page.request.post(`/api/presets/${PRESET_ID}/assign`, { headers, data: { user_ids: [userId] } });
	expect(assign.ok(), `assign -> ${assign.status()}`).toBeTruthy();
	return preset!.name;
}

async function seedGuideGeneration(page: Page, headers: Record<string, string>): Promise<{ id: string; url: string }> {
	const upload = await page.request.post('/api/generations/upload', {
		headers,
		multipart: { files: { name: 'guide-canny.png', mimeType: 'image/png', buffer: edgeMapPng(512) } }
	});
	expect(upload.ok(), `upload -> ${upload.status()}`).toBeTruthy();
	const id = (await upload.json()).data.generation_id as string;
	const dbPath = process.env.E2E_DB_PATH;
	expect(dbPath, 'E2E_DB_PATH must be set by tests/e2e/ui/run.py').toBeTruthy();
	const script = `
import sqlite3
conn = sqlite3.connect(${JSON.stringify(dbPath)}, timeout=30)
try:
    conn.execute("UPDATE generations SET preset_id=?, mode='control' WHERE id=?", (${JSON.stringify(PRESET_ID)}, ${JSON.stringify(id)}))
    conn.execute("UPDATE files SET label=? WHERE id IN (SELECT file_id FROM generation_files WHERE generation_id=?)", (${JSON.stringify(LABEL)}, ${JSON.stringify(id)}))
    conn.commit()
finally:
    conn.close()
`;
	execFileSync(process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3'), ['-c', script]);
	const detail = await page.request.get(`/api/generations/history/${id}?include_files=true`, { headers });
	expect(detail.ok(), `detail -> ${detail.status()}`).toBeTruthy();
	const files = ((await detail.json()).data.files || []) as Array<{ file_path: string; label?: string | null }>;
	expect(files[0]?.label, 'the saved file carries its caption').toBe(LABEL);
	const filename = files[0].file_path.split('/').pop();
	return { id, url: `/api/media/generations/${id}/${filename}` };
}

async function openControlTab(page: Page, name: string): Promise<Locator> {
	await page.goto('/generate');
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByText(name, { exact: true }).first().click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
	await page.getByTestId('preset-header-mode').getByRole('button').first().click();
	await page.getByRole('option', { name: /^Control/ }).first().click();
	const tab = page.getByRole('tab', { name: 'Control', exact: true }).first();
	await expect(tab).toBeVisible({ timeout: 20000 });
	await tab.click();
	await expect(page.getByText('Guide', { exact: true }).first()).toBeVisible();
	await page.waitForTimeout(400);
	return page.locator('body');
}

async function shoot(page: Page, label: string) {
	await page.mouse.move(page.viewportSize()!.width - 2, 2);
	await page.waitForTimeout(600);
	await screenshot(page, JOURNEY, label);
}

test('only extracting the guide needs no prompt and saves a labelled map', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await loginAsOwner(page);
	const token = await ownerToken(page);
	const headers = { Authorization: `Bearer ${token}` };
	const name = await prepare(page, headers);
	const seeded = await seedGuideGeneration(page, headers);

	const reattached = { statusServed: 0, subscribed: false };
	await page.route(`**/api/generations/${seeded.id}/status`, (route) => {
		reattached.statusServed += 1;
		return route.fulfill({
			json: {
				success: true,
				data: { id: seeded.id, generation_id: seeded.id, status: 'running', progress: 0.5, created_at: new Date().toISOString() }
			}
		});
	});
	const send: { fn: ((message: unknown) => void) | null } = { fn: null };
	await page.routeWebSocket('**/ws/generation*', (ws) => {
		send.fn = (message) => ws.send(JSON.stringify(message));
		ws.send(JSON.stringify({ type: 'connection_established' }));
		ws.onMessage((raw) => {
			const msg = JSON.parse(String(raw));
			if (msg.type === 'subscribe_generation' && msg.generation_id === seeded.id) reattached.subscribed = true;
			if (msg.type === 'subscribe_generation') ws.send(JSON.stringify({ type: 'subscribed', generation_id: msg.generation_id }));
			if (msg.type === 'ping') ws.send(JSON.stringify({ type: 'pong' }));
		});
	});

	const root = await openControlTab(page, name);
	const only = root.getByText('Only extract the guide', { exact: true }).first();
	const save = root.getByText('Also save the extracted guide', { exact: true });
	const missingPrompt = page.getByText('Missing prompt', { exact: true });
	const promptPane = page.getByText('Prompt', { exact: true });

	await expect(only).toBeVisible();
	await expect(save.first()).toBeVisible();
	await only.evaluate((el) => el.scrollIntoView({ block: 'center' }));
	await expect(missingPrompt).toBeVisible();
	await expect(promptPane.first()).toBeVisible();
	await shoot(page, '01-switches-off-prompt-required');

	await only.click();
	await expect(root.locator('[data-field-name="guide_only"] input[type="checkbox"]')).toBeChecked();
	await expect(save).toHaveCount(0);
	await expect(missingPrompt).toHaveCount(0, { timeout: 10000 });
	await expect(promptPane).toHaveCount(0);
	await only.evaluate((el) => el.scrollIntoView({ block: 'center' }));
	await shoot(page, '02-only-extract-no-prompt-needed');

	await page.evaluate(
		({ key, genId }) => {
			const raw = localStorage.getItem(key);
			if (!raw) return;
			const state = JSON.parse(raw);
			const active = state.tabs.find((t: { id: string }) => t.id === state.activeTabId) ?? state.tabs[0];
			active.activeGenerationId = genId;
			localStorage.setItem(key, JSON.stringify(state));
		},
		{ key: TABS_STORAGE_KEY, genId: seeded.id }
	);
	send.fn = null;
	reattached.statusServed = 0;
	reattached.subscribed = false;
	await page.reload();
	await page.waitForURL(/\/generate/, { timeout: 15000 });
	await expect.poll(() => send.fn !== null, { timeout: 20000 }).toBe(true);
	await expect.poll(() => reattached.statusServed > 0 && reattached.subscribed, { timeout: 30000 }).toBe(true);
	await page.waitForTimeout(1500);

	send.fn!({
		type: 'gallery_update',
		generation_id: seeded.id,
		pipe_id: 1,
		images: [seeded.url],
		image_urls_list: [{ original: seeded.url, derived: false, label: LABEL }]
	});
	send.fn!({ type: 'generation_complete', generation_id: seeded.id, pipe_id: 1 });
	const chips = page.locator('[data-output-label]');
	await expect(chips.first()).toBeVisible({ timeout: 15000 });
	await expect(chips.first()).toContainText(LABEL);
	await shoot(page, '03-workbench-guide-result');

	await page.goto('/history');
	const historyChip = page.locator('[data-output-label]').first();
	await expect(historyChip).toBeVisible({ timeout: 20000 });
	await expect(historyChip).toContainText(LABEL);
	await shoot(page, '04-history-guide-result');
});
