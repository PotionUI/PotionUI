import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'content-safety';
const TABS_STORAGE_KEY = 'potionui_tabs_state';
const GEN_ID = 'e2e-content-safety-gen';

function modelsStatus(present: boolean) {
	const model = { present, path: '/models/wd-tagger', size: present ? 340_000_000 : null, loaded: false, active_download: null };
	return { success: true, data: { tagger: model, vision: { ...model, present: false } } };
}

function safetyStatus(present: boolean, backfill = { total: 0, rated: 0, running: false }) {
	return {
		success: true,
		data: { policy: 'blocked', tagger: { present, device: 'cpu', downloading: false }, backfill }
	};
}

async function mockSafety(page: Page, present: boolean, backfill?: { total: number; rated: number; running: boolean }) {
	await page.route('**/api/content-safety/status', (route) =>
		route.fulfill({ json: safetyStatus(present, backfill) })
	);
	await page.route('**/api/media-index/models-status*', (route) => route.fulfill({ json: modelsStatus(present) }));
}

async function openPanel(page: Page) {
	await page.goto('/admin?tab=settings&view=content_safety');
	await expect(page.getByText('NSFW content', { exact: true })).toBeVisible({ timeout: 20000 });
}

test('admin panel: tagger missing', async ({ page }) => {
	await loginAsOwner(page);
	await mockSafety(page, false);
	await openPanel(page);
	await page.getByRole('button', { name: 'Blocked', exact: true }).click();
	await page.locator('#content-banned-words').fill('nud*\nred flag');
	await page.locator('#content-banned-test').fill('a study of nudity');

	await expect(page.locator('[data-tagger-status]')).toContainText('Not downloaded');
	await expect(page.getByRole('button', { name: 'Download and enable' })).toBeVisible();
	await expect(page.getByText('Blocked refuses every generation')).toBeVisible();
	await expect(page.locator('[data-banned-test-result]')).toContainText('Refused');
	await screenshot(page, JOURNEY, '01-admin-tagger-missing');
});

test('admin panel: tagger present with backfill', async ({ page }) => {
	await loginAsOwner(page);
	await mockSafety(page, true, { total: 480, rated: 190, running: true });
	await openPanel(page);
	await page.getByRole('button', { name: 'Blur', exact: true }).click();
	await page.locator('#content-banned-test').fill('a quiet harbor at dawn');

	await expect(page.locator('[data-tagger-status]')).toContainText('Ready');
	await expect(page.getByRole('button', { name: 'Download and enable' })).toHaveCount(0);
	await expect(page.locator('[data-backfill]')).toContainText('190 / 480');
	await expect(page.locator('[data-banned-test-result]')).toContainText('Would pass');
	await screenshot(page, JOURNEY, '02-admin-tagger-present');
});

test('workbench: hidden preview placeholder, then blocked notice tile', async ({ page }) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);
	const auth = { headers: { Authorization: `Bearer ${token}` } };

	const me = await (await page.request.get('/api/auth/me', auth)).json();
	const list = await (await page.request.get('/api/presets?include_uninstalled=true', auth)).json();
	const preset = ((list.data || []) as Array<{ id: string; installed?: boolean }>)[0];
	if (!preset) {
		test.skip(true, 'No presets available on this throwaway instance.');
		return;
	}
	if (!preset.installed) await page.request.post(`/api/presets/${preset.id}/install`, { ...auth, data: {} });
	await page.request.post(`/api/presets/${preset.id}/assign`, { ...auth, data: { user_ids: [me.data.id] } });
	const modes = await (await page.request.get(`/api/presets/${preset.id}/modes`, auth)).json();
	const modeName = modes.data?.modes?.[0]?.name as string | undefined;
	if (!modeName) {
		test.skip(true, 'Preset exposes no modes on this throwaway instance.');
		return;
	}

	await page.route(`**/api/generations/${GEN_ID}/status`, (route) =>
		route.fulfill({
			json: {
				success: true,
				data: { id: GEN_ID, generation_id: GEN_ID, status: 'running', progress: 0.4, created_at: new Date().toISOString() }
			}
		})
	);

	const send: { fn: ((message: unknown) => void) | null; subscribed: boolean } = { fn: null, subscribed: false };
	await page.routeWebSocket('**/ws/generation*', (ws) => {
		send.fn = (message) => ws.send(JSON.stringify(message));
		ws.send(JSON.stringify({ type: 'connection_established' }));
		ws.onMessage((raw) => {
			const msg = JSON.parse(String(raw));
			if (msg.type === 'subscribe_generation') {
				ws.send(JSON.stringify({ type: 'subscribed', generation_id: msg.generation_id }));
				if (msg.generation_id === GEN_ID) send.subscribed = true;
			}
			if (msg.type === 'ping') ws.send(JSON.stringify({ type: 'pong' }));
		});
	});

	await page.addInitScript(
		({ key, genId, presetId, mode }) => {
			const raw = localStorage.getItem(key);
			if (!raw) return;
			const state = JSON.parse(raw);
			if (!state?.tabs?.length) return;
			state.tabs[0].activeGenerationId = genId;
			state.tabs[0].selectedPreset = presetId;
			state.tabs[0].selectedMode = mode;
			localStorage.setItem(key, JSON.stringify(state));
		},
		{ key: TABS_STORAGE_KEY, genId: GEN_ID, presetId: preset.id, mode: modeName }
	);
	await page.reload();
	await page.waitForURL(/\/generate/, { timeout: 15000 });
	await expect.poll(() => send.fn !== null && send.subscribed, { timeout: 20000 }).toBe(true);

	send.fn!({ type: 'workbench_update', generation_id: GEN_ID, pipe_id: 1, preview_suppressed: true, file_type: 'image' });
	const preview = page.locator('[data-content-policy-tile="preview"]');
	await expect(preview).toBeVisible({ timeout: 10000 });
	await expect(preview).toContainText('Previews are hidden by the content policy');
	await screenshot(page, JOURNEY, '03-workbench-preview-hidden');

	send.fn!({ type: 'content_blocked', generation_id: GEN_ID, pipe_id: 1, blocked_count: 3, total: 4 });
	send.fn!({ type: 'gallery_update', generation_id: GEN_ID, images: [], image_urls_list: [] });
	const blocked = page.locator('[data-content-policy-tile="blocked"]');
	await expect(blocked).toBeVisible({ timeout: 10000 });
	await expect(blocked).toContainText('Blocked by content policy');
	await expect(blocked).toContainText('3 of 4 blocked');
	await screenshot(page, JOURNEY, '04-workbench-blocked');
});
