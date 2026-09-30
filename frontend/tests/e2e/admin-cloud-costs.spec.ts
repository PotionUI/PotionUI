import { test, expect, type Locator, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'admin-cloud-costs';

async function json(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
}

async function reveal(locator: Locator) {
	await locator.scrollIntoViewIfNeeded();
}

const LONG_TITLE = 'Cloud Text To Video With An Extraordinarily Long Preset Name That Keeps Going And Going';

const SCOPE_BASE = {
	model_id: 'm-cloud',
	slug: 'openrouter~veo-3-1',
	label: 'Veo 3.1',
	driver: 'cloud.openrouter',
	candidates: [
		{ id: 'p-t2i', title: 'Cloud Text To Image' },
		{ id: 'p-edit', title: 'Cloud Image Edit' },
		{ id: 'p-t2v', title: LONG_TITLE },
		{ id: 'p-i2v', title: 'Cloud Image To Video' }
	]
};

function scopeData(ids: string[], missing: string[] = []) {
	const all = [...ids, ...missing];
	return {
		...SCOPE_BASE,
		scoped: all.length > 0,
		preset_ids: all,
		presets: all.map((id) => {
			const found = SCOPE_BASE.candidates.find((c) => c.id === id);
			return found
				? { id, title: found.title, engine: 'cloud', driver: 'cloud.openrouter', missing: false, compatible: true }
				: { id, title: null, engine: null, driver: null, missing: true, compatible: false };
		})
	};
}

const CLOUD_MODEL = {
	id: 'm-cloud',
	filename: 'openrouter~veo-3-1',
	name: 'Veo 3.1',
	display_name: 'Veo 3.1',
	model_type: 'cloud',
	sha256: null,
	file_size: null,
	is_directory: false,
	copies: 0,
	location: null,
	created_at: '2026-09-30T10:00:00Z',
	indexed_at: null,
	description: '',
	prompting_guidance: null,
	tags: [],
	files: [],
	providers: [{ provider: 'cloud.openrouter', tags: [] }]
};

const FILE_MODEL = {
	...CLOUD_MODEL,
	id: 'm-file',
	filename: 'sdxl-base.safetensors',
	name: 'SDXL Base',
	display_name: 'SDXL Base',
	model_type: 'checkpoint',
	sha256: 'a'.repeat(64),
	file_size: 6938078334,
	providers: []
};

interface ScopeMocks {
	puts: string[][];
	current: { ids: string[]; missing: string[] };
	putFailure: boolean;
	getFailure: boolean;
}

async function mockModels(page: Page): Promise<ScopeMocks> {
	const mocks: ScopeMocks = { puts: [], current: { ids: ['p-t2i', 'p-t2v'], missing: ['p-gone'] }, putFailure: false, getFailure: false };
	await page.route(
		(url) => url.pathname === '/api/models' || url.pathname.startsWith('/api/models/m-') || url.pathname.startsWith('/api/cloud/models/'),
		async (route) => {
			const request = route.request();
			const path = new URL(request.url()).pathname;
			if (path === '/api/models') {
				return json(route, { success: true, data: { models: [CLOUD_MODEL, FILE_MODEL], total: 2, availability_indexed: true } });
			}
			if (path === '/api/models/m-cloud') return json(route, { success: true, data: { model: CLOUD_MODEL } });
			if (path === '/api/models/m-file') return json(route, { success: true, data: { model: FILE_MODEL } });
			const scope = path.match(/^\/api\/cloud\/models\/([^/]+)\/scope$/);
			if (scope) {
				if (request.method() === 'GET') {
					if (mocks.getFailure) return json(route, { success: false, error: 'boom', message: 'The scope could not be read.' }, 500);
					return json(route, { success: true, data: scopeData(mocks.current.ids, mocks.current.missing) });
				}
				const { preset_ids } = request.postDataJSON() as { preset_ids: string[] };
				mocks.puts.push(preset_ids);
				if (mocks.putFailure) {
					return json(
						route,
						{ detail: { error: 'cloud_scope_invalid', message: "Preset 'Cloud Image Edit' cannot use this model: it runs on cloud/openrouter-edit, not cloud.openrouter." } },
						422
					);
				}
				mocks.current = { ids: preset_ids, missing: [] };
				return json(route, { success: true, data: scopeData(preset_ids) });
			}
			return route.fallback();
		}
	);
	return mocks;
}

function genRow(id: string, preset: string, cost: unknown, status = 'completed') {
	return {
		id,
		form_data: {},
		status,
		progress: 1,
		created_at: new Date(Date.now() - 3600 * 1000).toISOString(),
		completed_at: new Date(Date.now() - 3540 * 1000).toISOString(),
		updated_at: new Date(Date.now() - 3540 * 1000).toISOString(),
		files: [],
		rating: 0,
		is_favorite: false,
		user_id: 'u-alice',
		has_run_report: false,
		preset_name: preset,
		mode: 'txt2video',
		cost
	};
}

const ROWS = [
	genRow('g1', 'Cloud Text To Video', { amount_usd: '0.0731', source: 'provider', entries: 1, unpriced: 0 }),
	genRow('g2', 'Cloud Image Edit', { amount_usd: '0.04', source: 'estimate', entries: 1, unpriced: 0 }),
	genRow('g3', 'Cloud Image To Video', { amount_usd: '1.25', source: 'mixed', entries: 3, unpriced: 1 }),
	genRow('g4', 'Cloud Text To Image', { amount_usd: null, source: 'unknown', entries: 1, unpriced: 1 }),
	genRow('g5', 'Flux Dev (local)', null)
];

const COST_DETAIL = {
	amount_usd: '1.25',
	source: 'mixed',
	entries: 3,
	unpriced: 1,
	items: [
		{ id: 'c1', backend_id: 'backend-openrouter-team', model_id: 'm-cloud', amount_usd: '1.1', source: 'provider', detail: { task: 'img2video' }, created_at: new Date(Date.now() - 3550 * 1000).toISOString() },
		{ id: 'c2', backend_id: 'backend-openrouter-team', model_id: 'm-cloud', amount_usd: '0.15', source: 'estimate', detail: { task: 'txt2img' }, created_at: new Date(Date.now() - 3545 * 1000).toISOString() },
		{ id: 'c3', backend_id: 'backend-openrouter-team', model_id: 'm-cloud', amount_usd: null, source: 'unknown', detail: {}, created_at: new Date(Date.now() - 3541 * 1000).toISOString() }
	]
};

async function mockGenerations(page: Page) {
	await page.route(
		(url) => url.pathname.startsWith('/api/admin/generations') || url.pathname === '/api/users',
		async (route) => {
			const request = route.request();
			const path = new URL(request.url()).pathname;
			if (request.method() !== 'GET') return route.fallback();
			if (path === '/api/users') {
				return json(route, { success: true, data: [{ id: 'u-alice', username: 'alice', email: 'a@example.com', account_type: 'USER' }] });
			}
			if (path === '/api/admin/generations/queue') return json(route, { success: true, data: { running: [], pending: [] } });
			if (path === '/api/admin/generations') return json(route, { success: true, data: { generations: ROWS, total: ROWS.length } });
			const detail = path.match(/^\/api\/admin\/generations\/([^/]+)$/);
			if (detail) {
				const row = ROWS.find((r) => r.id === detail[1]);
				if (row) return json(route, { success: true, data: { generation: { ...row, routing: null }, run_report: null, cost: detail[1] === 'g3' ? COST_DETAIL : null } });
			}
			return route.fallback();
		}
	);
}

const SPEND = {
	from: null,
	to: null,
	total_usd: '42.8137',
	entries: 131,
	unpriced: 4,
	by_backend: [
		{ backend_id: 'b1', backend_name: 'OpenRouter (team key)', amount_usd: '39.1', source: 'mixed', entries: 120, unpriced: 4 },
		{ backend_id: 'b2', backend_name: 'Second provider with a very long configured backend name', amount_usd: '3.7137', source: 'estimate', entries: 11, unpriced: 0 }
	],
	by_model: [
		{ model_id: 'm1', model: 'Veo 3.1', amount_usd: '30.2', source: 'provider', entries: 40, unpriced: 0 },
		{ model_id: 'm2', model: 'Seedream 4.5 with an unusually long upstream display label', amount_usd: '8.9', source: 'mixed', entries: 70, unpriced: 4 },
		{ model_id: 'm3', model: 'Flux 2 Pro', amount_usd: '3.7137', source: 'estimate', entries: 11, unpriced: 0 },
		{ model_id: 'm4', model: null, amount_usd: null, source: 'unknown', entries: 10, unpriced: 10 }
	]
};

const EMPTY_SPEND = { from: null, to: null, total_usd: '0', entries: 0, unpriced: 0, by_backend: [], by_model: [] };

for (const viewport of [
	{ name: '1440', width: 1440, height: 900 },
	{ name: '390', width: 390, height: 844 }
]) {
	test.describe(`admin cloud costs @${viewport.name}`, () => {
		test.use({ viewport: { width: viewport.width, height: viewport.height } });

		test('model detail: Allowed in presets loads, edits, saves and surfaces a 422', async ({ page }) => {
			const mocks = await mockModels(page);
			await loginAsOwner(page);
			await page.goto('/admin?tab=models&id=m-cloud');
			const section = page.locator('section', { has: page.getByRole('heading', { name: 'Allowed in presets' }) });
			await expect(section).toBeVisible({ timeout: 15000 });
			await expect(section).toContainText('offered in every compatible preset');
			await expect(section.locator('[data-scope-row]')).toHaveCount(3);
			await expect(section).toContainText('missing');
			await expect(page.getByRole('heading', { name: 'Model type' })).toHaveCount(0);
			await expect(page.getByRole('tab', { name: 'Availability' })).toHaveCount(0);
			await reveal(section);
			await screenshot(page, JOURNEY, `scope-loaded-${viewport.name}`);

			await section.getByRole('button', { name: 'Remove Cloud Text To Image' }).click();
			await section.locator('input[type="text"]').click();
			await page.getByRole('option', { name: 'Cloud Image Edit' }).click();
			await expect(section.locator('[data-scope-row]')).toHaveCount(3);
			await expect(page.locator('[data-detail-footer]')).toContainText('unsaved');

			mocks.putFailure = true;
			await page.locator('[data-detail-footer]').getByRole('button', { name: 'Save' }).click();
			await expect(section.getByRole('alert')).toContainText('cannot use this model');
			await reveal(section);
			await screenshot(page, JOURNEY, `scope-422-${viewport.name}`);

			mocks.putFailure = false;
			await section.getByRole('button', { name: 'Remove Cloud Image Edit' }).click();
			await page.locator('[data-detail-footer]').getByRole('button', { name: 'Save' }).click();
			await expect.poll(() => mocks.puts.length).toBe(2);
			expect(mocks.puts[1]).toEqual(['p-t2v']);
			await expect(section.locator('[data-scope-row]')).toHaveCount(1);
			await expect(section).not.toContainText('missing');
			await reveal(section);
			await screenshot(page, JOURNEY, `scope-saved-${viewport.name}`);
		});

		test('model detail: empty scope and a file model', async ({ page }) => {
			const mocks = await mockModels(page);
			mocks.current = { ids: [], missing: [] };
			await loginAsOwner(page);
			await page.goto('/admin?tab=models&id=m-cloud');
			const section = page.locator('section', { has: page.getByRole('heading', { name: 'Allowed in presets' }) });
			await expect(section.locator('[data-scope-empty]')).toBeVisible({ timeout: 15000 });
			await reveal(section);
			await screenshot(page, JOURNEY, `scope-empty-${viewport.name}`);

			await page.goto('/admin?tab=models&id=m-file');
			await expect(page.getByRole('heading', { name: 'Model type' })).toBeVisible({ timeout: 15000 });
			await expect(page.getByRole('heading', { name: 'Allowed in presets' })).toHaveCount(0);
		});

		test('model detail: load error offers retry', async ({ page }) => {
			const mocks = await mockModels(page);
			mocks.getFailure = true;
			await loginAsOwner(page);
			await page.goto('/admin?tab=models&id=m-cloud');
			const section = page.locator('section', { has: page.getByRole('heading', { name: 'Allowed in presets' }) });
			await expect(section).toContainText('The scope could not be read.', { timeout: 15000 });
			mocks.getFailure = false;
			await expect(async () => {
				const retry = section.getByRole('button', { name: 'Retry' });
				if (await retry.count()) await retry.click({ timeout: 1000 });
				await expect(section.locator('[data-scope-row]')).toHaveCount(3, { timeout: 1500 });
			}).toPass({ timeout: 15000 });
		});

		test('generations: cost column and detail', async ({ page }) => {
			await mockGenerations(page);
			await loginAsOwner(page);
			await page.goto('/admin?tab=generations');
			await expect(page.getByText('Cloud Text To Video').locator('visible=true').first()).toBeVisible({ timeout: 15000 });
			if (viewport.width > 600) {
				await expect(page.locator('[data-cost="amount"]').first()).toContainText('$0.07');
				await expect(page.locator('[data-cost-estimate]').first()).toBeVisible();
				await expect(page.locator('[data-cost="unpriced"]').first()).toBeVisible();
				await expect(page.locator('[data-cost="none"]').first()).toBeVisible();
			}
			await screenshot(page, JOURNEY, `generations-list-${viewport.name}`);

			await page.goto('/admin?tab=generations&id=g3');
			const cost = page.locator('[data-generation-cost]');
			await expect(cost).toBeVisible({ timeout: 15000 });
			await expect(cost.locator('[data-cost-item]')).toHaveCount(3);
			await expect(cost).toContainText('Image to video');
			await reveal(cost);
			await screenshot(page, JOURNEY, `generations-detail-${viewport.name}`);
		});

		test('stats: cloud spend section and its empty state', async ({ page }) => {
			let spend: unknown = SPEND;
			await page.route(
				(url) => url.pathname === '/api/stats/spend',
				(route) => json(route, { success: true, data: spend })
			);
			await loginAsOwner(page);
			await page.goto('/admin?tab=stats');
			const section = page.locator('[data-cloud-spend]');
			await expect(section).toBeVisible({ timeout: 15000 });
			await expect(section.locator('[data-spend-total]')).toHaveText('$42.81');
			await expect(section.locator('[data-spend-table="By model"] [data-spend-row]').first()).toContainText('Veo 3.1');
			await expect(section).toContainText('4 jobs with no known price');
			await reveal(section);
			await screenshot(page, JOURNEY, `stats-spend-${viewport.name}`);

			spend = EMPTY_SPEND;
			await page.reload();
			await expect(page.getByRole('button', { name: 'Apply' })).toBeEnabled({ timeout: 15000 });
			await expect(page.locator('[data-cloud-spend]')).toHaveCount(0);
			await screenshot(page, JOURNEY, `stats-no-spend-${viewport.name}`);
		});
	});
}
