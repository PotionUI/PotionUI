import type { Page } from '@playwright/test';
import { clearTabDrafts, ownerToken } from './helpers';

export const SLOTS = ['diffusion_model', 'text_encoder', 'vae'] as const;
const GENERATION_ID = '01FORMSEEDGENERATION00001';

function modelRow(id: string, modelType: string) {
	const now = new Date().toISOString();
	return {
		id,
		filename: `${id}.safetensors`,
		model_type: modelType,
		file_size: 4_000_000_000,
		sha256: id.padEnd(64, '0'),
		created_at: now,
		updated_at: now,
		indexed_at: now,
		is_available: true,
		is_favorite: false,
		tags: [],
		providers: [],
		files: [],
		backend_ids: ['native']
	};
}

export async function mockInstalledModels(page: Page, perSlot: number) {
	const searches: string[] = [];
	await page.route(
		(url) => /^\/api\/presets\/[^/]+\/models$/.test(url.pathname),
		async (route) => {
			if (route.request().method() !== 'GET') return route.fallback();
			const type = new URL(route.request().url()).searchParams.get('model_type') ?? 'checkpoint';
			searches.push(type);
			const models = Array.from({ length: perSlot }, (_, index) => modelRow(`e2e-${type}-${index + 1}`, type));
			await route.fulfill({
				json: {
					success: true,
					data: { engine: 'native', models, total: models.length, indexed: true, availability_indexed: true }
				}
			});
		}
	);
	return searches;
}

export async function captureGenerationStart(page: Page) {
	const calls: Array<{ body: any; status: number }> = [];
	await page.route('**/api/generations/start', async (route) => {
		const body = route.request().postDataJSON();
		const formData = body?.form_data ?? {};
		const missing = SLOTS.filter((slot) => typeof formData[slot] !== 'string' || !formData[slot]);
		if (missing.length > 0) {
			calls.push({ body, status: 422 });
			await route.fulfill({
				status: 422,
				json: {
					error: 'form_validation_failed',
					field_errors: Object.fromEntries(missing.map((slot) => [slot, ['This field is required.']]))
				}
			});
			return;
		}
		calls.push({ body, status: 200 });
		await route.fulfill({
			json: {
				success: true,
				data: {
					generation_id: GENERATION_ID,
					status: {
						id: GENERATION_ID,
						generation_id: GENERATION_ID,
						status: 'pending',
						progress: 0.0,
						created_at: new Date().toISOString()
					},
					queue_position: null
				}
			}
		});
	});
	return calls;
}

export async function assignPreset(page: Page, name: string) {
	const token = await ownerToken(page);
	const headers = { Authorization: `Bearer ${token}` };
	const list = await (await page.request.get('/api/presets?include_uninstalled=true', { headers })).json();
	const preset = ((list.data || []) as Array<{ id: string; name: string; installed?: boolean }>).find(
		(candidate) => candidate.name === name
	);
	if (!preset) return null;
	if (!preset.installed) await page.request.post(`/api/presets/${preset.id}/install`, { headers, data: {} });
	const me = await (await page.request.get('/api/auth/me', { headers })).json();
	await page.request.post(`/api/presets/${preset.id}/assign`, { headers, data: { user_ids: [me.data.id] } });
	return preset;
}

export async function startFromNoTabs(page: Page) {
	await clearTabDrafts(page);
	await page.evaluate(() => {
		for (const key of Object.keys(localStorage)) if (key.startsWith('potionui_tabs_state')) localStorage.removeItem(key);
	});
}
