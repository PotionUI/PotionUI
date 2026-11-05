import { expect, type Locator, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken } from './helpers';

export const FAKE_PRESET = '01M3SVA35XT0E7CG5JZTS9P21S';
export const BACKEND_NAME = 'E2E Fake Cloud';
export function headers(token: string) {
	return { Authorization: `Bearer ${token}` };
}

export async function api(
	page: Page,
	token: string,
	method: 'get' | 'post' | 'put' | 'delete',
	url: string,
	data?: unknown
) {
	const response = await page.request[method](url, { headers: headers(token), data });
	const text = await response.text();
	expect(response.ok(), `${method.toUpperCase()} ${url} -> ${response.status()} ${text}`).toBeTruthy();
	return text ? JSON.parse(text) : null;
}

export async function setKnobs(page: Page, token: string, knobs: Record<string, unknown>) {
	const backends = await api(page, token, 'get', '/api/backends');
	const backend = backends.data.find((item: any) => item.name === BACKEND_NAME);
	const current = await api(page, token, 'get', `/api/backends/${backend.id}`);
	await api(page, token, 'put', `/api/backends/${backend.id}`, { ...current.data, ...knobs });
	return backend.id;
}

export async function prepare(page: Page) {
	await loginAsOwner(page);
	const token = await ownerToken(page);
	const backendId = await setKnobs(page, token, { duration_seconds: 1.0, fail_kind: '' });
	await api(page, token, 'post', `/api/cloud/backends/${backendId}/catalog/enable`, { slugs: ['fake~fake~image-1'] });
	const catalog = await api(page, token, 'get', `/api/cloud/backends/${backendId}/catalog?enabled=true`);
	const model = catalog.data.items.find((entry: any) => entry.slug === 'fake~fake~image-1');
	expect(model?.model_id, 'the fake image model must be enabled').toBeTruthy();
	return { token, backendId, modelRef: `model:${model.model_id}` };
}

export async function seedElixir(
	page: Page,
	token: string,
	modelRef: string,
	runMode: 'automatic' | 'review',
	name: string = `Brew ${runMode}`
) {
	const form = { model: modelRef, count: 1, aspect_ratio: '1:1' };
	const photo = await api(page, token, 'post', '/api/sessions/save', {
		preset_id: FAKE_PRESET,
		name: `Potion photo ${name}`,
		data: { txt2img: { prompt: 'a glass potion on a wooden table', formData: form } }
	});
	const restyle = await api(page, token, 'post', '/api/sessions/save', {
		preset_id: FAKE_PRESET,
		name: `Blue restyle ${name}`,
		data: { edit: { prompt: 'make the liquid deep blue', formData: form } }
	});
	const created = await api(page, token, 'post', '/api/elixirs', {
		name,
		run_mode: runMode,
		definition: {
			nodes: [
				{ id: 'photo', kind: 'preset', title: 'Potion photo', position: { x: 40, y: 80 }, preset_id: FAKE_PRESET, mode: 'txt2img' },
				{ id: 'restyle', kind: 'preset', title: 'Blue restyle', position: { x: 500, y: 80 }, preset_id: FAKE_PRESET, mode: 'edit' }
			],
			edges: [{ id: 'e1', source: 'photo', source_handle: 'images', target: 'restyle', target_handle: 'references' }]
		}
	});
	let revision = created.revision;
	for (const [nodeId, session] of [['photo', photo.data.id], ['restyle', restyle.data.id]]) {
		const imported = await api(page, token, 'post', `/api/elixirs/${created.id}/nodes/${nodeId}/session`, {
			expected_revision: revision,
			session_id: session
		});
		revision = imported.revision;
	}
	return created.id as string;
}

export async function createBlank(page: Page, token: string, name: string) {
	const created = await api(page, token, 'post', '/api/elixirs', { name });
	return created.id as string;
}

export async function clearElixirs(page: Page, token: string) {
	const list = await api(page, token, 'get', '/api/elixirs');
	for (const item of list.elixirs) {
		const runs = await page.request.get(`/api/elixirs/${item.id}/runs`, { headers: headers(token) });
		for (const run of runs.ok() ? (await runs.json()).runs : []) {
			if (['pending', 'running', 'awaiting_selection', 'needs_attention', 'cancelling'].includes(run.status)) {
				await page.request.post(`/api/elixirs/runs/${run.id}/cancel`, { headers: headers(token), data: {} });
			}
		}
		await expect
			.poll(async () => (await page.request.delete(`/api/elixirs/${item.id}`, { headers: headers(token) })).status(), {
				timeout: 30000,
				message: `elixir ${item.name} could not be removed`
			})
			.not.toBe(409);
		await expect
			.poll(async () => (await page.request.get(`/api/elixirs/${item.id}`, { headers: headers(token) })).status(), { timeout: 30000 })
			.toBe(404);
	}
}

export function editorOf(page: Page, elixirId: string): Locator {
	return page.locator(`[data-testid="elixir-tab"][data-elixir-id="${elixirId}"]`);
}

export async function openElixir(page: Page, elixirId: string): Promise<Locator> {
	await page.goto(`/elixirs/${elixirId}`);
	await page.waitForURL('**/generate');
	const editor = editorOf(page, elixirId);
	await expect(editor).toBeVisible({ timeout: 30000 });
	return editor;
}

export function stripTab(page: Page, name: string | RegExp): Locator {
	return page.locator('.book-tab-wrapper').filter({ hasText: name });
}

export const brewStatus = (editor: Locator) => editor.getByRole('status', { name: 'Brew status' });

export const problems = (editor: Locator) => editor.getByRole('button', { name: /problem/ });

export async function noHorizontalScroll(page: Page) {
	const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
	expect(overflow).toBeLessThanOrEqual(0);
}
