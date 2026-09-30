import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'admin-cloud-catalog';

const ENGINES = [
	{ engine: 'native', driver: 'native.local', label: 'Native (local)', singleton: true, creatable: false, fields: [] },
	{ engine: 'cloud', driver: 'cloud.fake', label: 'Fake Cloud', singleton: false, creatable: true, fields: [] }
];

function backend(id: string, name: string, engine: string, driver: string, isDefault: boolean) {
	return {
		id,
		name,
		engine,
		driver,
		enabled: true,
		is_default: isDefault,
		priority: 1,
		timeout_seconds: 300,
		scheduling_policy: 'fifo',
		scheduling_max_consecutive_same_model: 3,
		configured: true,
		quick_actions: []
	};
}

const BACKENDS = [
	backend('local', 'Local GPU', 'native', 'native.local', true),
	backend('cloud1', 'Fake Cloud (team key)', 'cloud', 'cloud.fake', false)
];

interface Entry {
	slug: string;
	provider_model_id: string;
	label: string;
	vendor: string | null;
	description: string | null;
	tasks: string[];
	outputs: string[];
	enabled: boolean;
	suggested: boolean;
	available: boolean;
	missing_since: string | null;
	deprecated: boolean;
	deprecated_at: string | null;
	discovered_at: string | null;
	refreshed_at: string | null;
	enabled_at: string | null;
	model_id: string | null;
	max_outputs_per_job: number;
	typical_seconds: number | null;
	max_seconds: number | null;
	params: unknown[];
	inputs: unknown[];
	pricing: { unit: string; usd: string; applies_to: string | null }[];
}

function entry(slug: string, overrides: Partial<Entry>): Entry {
	return {
		slug,
		provider_model_id: `vendor/${slug}`,
		label: slug,
		vendor: 'vendor',
		description: null,
		tasks: ['txt2img'],
		outputs: ['image'],
		enabled: false,
		suggested: false,
		available: true,
		missing_since: null,
		deprecated: false,
		deprecated_at: null,
		discovered_at: null,
		refreshed_at: null,
		enabled_at: null,
		model_id: null,
		max_outputs_per_job: 1,
		typical_seconds: null,
		max_seconds: null,
		params: [],
		inputs: [],
		pricing: [],
		...overrides
	};
}

function fixtureEntries(): Entry[] {
	const specials = [
		entry('veo-3-1', {
			provider_model_id: 'google/veo-3.1',
			label: 'Veo 3.1',
			vendor: 'google',
			tasks: ['txt2video', 'img2video'],
			outputs: ['video'],
			enabled: true,
			model_id: 'm-veo',
			deprecated: true,
			deprecated_at: '2026-10-01T00:00:00Z',
			pricing: [
				{ unit: 'second', usd: '0.40', applies_to: '1080p' },
				{ unit: 'second', usd: '0.20', applies_to: '720p' }
			]
		}),
		entry('seedream-4-5', {
			provider_model_id: 'bytedance/seedream-4.5',
			label: 'Seedream 4.5',
			vendor: 'bytedance',
			tasks: ['txt2img', 'img_edit'],
			suggested: true,
			pricing: [{ unit: 'image', usd: '0.04', applies_to: null }]
		}),
		entry('flux-2-pro', {
			provider_model_id: 'black-forest-labs/flux-2-pro',
			label: 'Flux 2 Pro',
			vendor: 'black-forest-labs',
			tasks: ['txt2img', 'img_edit', 'inpaint', 'ref2video', 'upscale_image'],
			enabled: true,
			model_id: 'm-flux',
			pricing: [{ unit: 'megapixel', usd: '0.03', applies_to: null }]
		}),
		entry('the-extraordinarily-long-model-name', {
			provider_model_id: 'an-upstream-organisation-with-a-long-name/the-extraordinarily-long-model-name-v12-preview-0925',
			label: 'The Extraordinarily Long Model Name With A Very Descriptive Title v12 Preview',
			vendor: 'an-upstream-organisation-with-a-long-name',
			tasks: ['txt2img', 'txt2video', 'img2video', 'video2video'],
			pricing: [
				{ unit: 'token', usd: '0.0000003', applies_to: 'input' },
				{ unit: 'request', usd: '0.01', applies_to: null }
			]
		}),
		entry('retired', {
			provider_model_id: 'old/retired-model',
			label: 'Retired Model',
			vendor: 'old',
			available: false,
			missing_since: '2026-09-20T00:00:00Z',
			pricing: []
		}),
		entry('broken', {
			provider_model_id: 'flaky/broken',
			label: 'Model That Fails To Enable',
			vendor: 'flaky',
			pricing: [{ unit: 'image', usd: '0.0005', applies_to: null }]
		})
	];
	const filler = Array.from({ length: 60 }, (_, index) =>
		entry(`filler-${index}`, {
			provider_model_id: `acme/filler-${index}`,
			label: `Filler Model ${index}`,
			vendor: 'acme',
			pricing: [{ unit: 'image', usd: '0.02', applies_to: null }]
		})
	);
	return [...specials, ...filler];
}

async function json(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
}

interface Mocks {
	entries: Entry[];
	refreshedAt: string | null;
	catalogQueries: URLSearchParams[];
	toggles: { slugs: string[]; enable: boolean }[];
	refreshes: number;
	refreshResponse: { status: number; body: unknown } | null;
	catalogFailure: boolean;
}

const DEFAULT_REFRESH = {
	backend_id: 'cloud1',
	refreshed_at: '2026-09-30T10:00:00Z',
	listed: 66,
	accepted: 64,
	created: 3,
	vanished: ['old/retired-model'],
	skipped: [
		{ provider_model_id: 'odd/no-outputs', problems: ['no outputs'] },
		{ provider_model_id: 'odd/bad-price', problems: ['negative price', 'unknown price unit \'furlong\''] }
	],
	index: null,
	empty: false,
	message: null
};

async function mockCloud(page: Page, options: { entries?: Entry[]; refreshedAt?: string | null } = {}): Promise<Mocks> {
	const mocks: Mocks = {
		entries: options.entries ?? fixtureEntries(),
		refreshedAt: options.refreshedAt === undefined ? new Date(Date.now() - 3 * 3600 * 1000).toISOString() : options.refreshedAt,
		catalogQueries: [],
		toggles: [],
		refreshes: 0,
		refreshResponse: null,
		catalogFailure: false
	};
	await page.route(
		(url) => url.pathname.startsWith('/api/backends') || url.pathname.startsWith('/api/cloud'),
		async (route) => {
			const request = route.request();
			const url = new URL(request.url());
			const path = url.pathname;
			const method = request.method();
			if (path === '/api/backends/engines') return json(route, { success: true, data: ENGINES });
			if (path === '/api/backends/health') {
				return json(route, { success: true, data: BACKENDS.map((b) => ({ backend_id: b.id, health: { status: 'healthy' } })) });
			}
			if (path === '/api/backends' && method === 'GET') return json(route, { success: true, data: BACKENDS });
			if (path.endsWith('/stats')) {
				return json(route, {
					success: true,
					data: { backend_id: 'local', indexed_models: 0, total_size_bytes: 0, total_size_gb: 0, last_indexed_at: null }
				});
			}
			const catalog = path.match(/^\/api\/cloud\/backends\/([^/]+)\/catalog$/);
			if (catalog && method === 'GET') {
				mocks.catalogQueries.push(url.searchParams);
				if (mocks.catalogFailure) {
					return json(route, { success: false, error: 'boom', message: 'The catalog could not be read.' }, 500);
				}
				const params = url.searchParams;
				const task = params.get('task');
				const output = params.get('output');
				const search = (params.get('search') ?? '').toLowerCase();
				const filtered = mocks.entries.filter(
					(e) =>
						(!task || e.tasks.includes(task)) &&
						(!output || e.outputs.includes(output)) &&
						(params.get('enabled') !== 'true' || e.enabled) &&
						(!search || `${e.label} ${e.provider_model_id}`.toLowerCase().includes(search))
				);
				const limit = Number(params.get('limit') ?? 50);
				const offset = Number(params.get('offset') ?? 0);
				return json(route, {
					success: true,
					data: {
						backend_id: catalog[1],
						driver: 'cloud.fake',
						total: filtered.length,
						limit,
						offset,
						provider: {
							key: 'fake',
							label: 'Fake Cloud',
							data_notice:
								'Prompts and reference images are sent to Fake Cloud and to the upstream hosts it picks for each model. Fake Cloud may keep them for abuse checks.',
							supports_cancel: false
						},
						state: mocks.refreshedAt ? { refreshed_at: mocks.refreshedAt, listed: mocks.entries.length, skipped: [] } : null,
						counts: {
							total: mocks.entries.length,
							enabled: mocks.entries.filter((e) => e.enabled).length,
							missing: mocks.entries.filter((e) => !e.available).length
						},
						items: filtered.slice(offset, offset + limit)
					}
				});
			}
			if (path.endsWith('/catalog/refresh') && method === 'POST') {
				mocks.refreshes += 1;
				await new Promise((resolve) => setTimeout(resolve, 400));
				if (mocks.refreshResponse) return json(route, mocks.refreshResponse.body, mocks.refreshResponse.status);
				mocks.refreshedAt = new Date().toISOString();
				return json(route, { success: true, data: DEFAULT_REFRESH });
			}
			const toggle = path.match(/\/catalog\/(enable|disable)$/);
			if (toggle && method === 'POST') {
				const enable = toggle[1] === 'enable';
				const { slugs } = request.postDataJSON() as { slugs: string[] };
				mocks.toggles.push({ slugs, enable });
				await new Promise((resolve) => setTimeout(resolve, 400));
				if (slugs.includes('broken')) {
					return json(route, { detail: { error: 'cloud_backend_inactive', message: 'The provider refused to enable this model.' } }, 409);
				}
				const models: Record<string, string> = {};
				for (const e of mocks.entries) {
					if (slugs.includes(e.slug)) {
						e.enabled = enable;
						if (enable) models[e.slug] = `model-${e.slug}`;
					}
				}
				return json(route, {
					success: true,
					data: { backend_id: 'cloud1', enabled: enable, changed: slugs, unchanged: [], models, index: null }
				});
			}
			return route.continue();
		}
	);
	return mocks;
}

async function openCatalog(page: Page, query = '') {
	await loginAsOwner(page);
	await page.goto(`/admin?tab=backends&backend=cloud1&view=catalog${query}`);
	await expect(page.getByRole('button', { name: 'Refresh catalog' }).first()).toBeVisible({ timeout: 15000 });
}

const gridRow = (page: Page, label: string) =>
	page.locator('.dt-scroll > .dt-row:not(.dt-row--head)', { hasText: label }).first();
const cardRow = (page: Page, label: string) => page.locator('.dt-mobile > div', { hasText: label }).first();

for (const viewport of [
	{ name: '1440', width: 1440, height: 900, mobile: false },
	{ name: '390', width: 390, height: 844, mobile: true }
]) {
	test.describe(`admin cloud catalog @${viewport.name}`, () => {
		test.use({ viewport: { width: viewport.width, height: viewport.height } });

		const modelRow = (page: Page, label: string) => (viewport.mobile ? cardRow(page, label) : gridRow(page, label));

		test('lists models with prices, statuses, the provider notice and paging', async ({ page }) => {
			await mockCloud(page);
			await openCatalog(page);
			await expect(modelRow(page, 'Veo 3.1')).toBeVisible();
			await expect(page.getByTestId('catalog-summary')).toContainText('Last refreshed 3h ago · 66 models · 2 enabled');
			await expect(page.getByTestId('catalog-notice')).toContainText('upstream hosts');
			await expect(modelRow(page, 'Seedream 4.5')).toContainText('$0.04 / image');
			await expect(modelRow(page, 'Veo 3.1')).toContainText('Deprecated');
			await expect(modelRow(page, 'Retired Model')).toContainText('Missing from provider');
			await expect(page.getByText('Page 1 of 2')).toBeVisible();
			await screenshot(page, JOURNEY, `list-${viewport.name}`);
			await page.getByText('Page 1 of 2').scrollIntoViewIfNeeded();
			await screenshot(page, JOURNEY, `pager-${viewport.name}`);
		});

		test('filters go into the URL and narrow the list', async ({ page }) => {
			const mocks = await mockCloud(page);
			await openCatalog(page);
			await page.getByRole('button', { name: /Filters/ }).first().click();
			const dialog = page.getByRole('dialog', { name: 'Catalog filters' });
			await expect(dialog).toBeVisible();
			await dialog.getByLabel('Task').selectOption('txt2video');
			await screenshot(page, JOURNEY, `filter-popover-${viewport.name}`);
			await dialog.getByRole('button', { name: 'Enabled only' }).click();
			await dialog.getByRole('button', { name: 'Done' }).click();
			await expect(page).toHaveURL(/task=txt2video/);
			await expect(page).toHaveURL(/enabled=1/);
			await expect(modelRow(page, 'Veo 3.1')).toBeVisible();
			await expect(modelRow(page, 'Seedream 4.5')).toHaveCount(0);
			const last = mocks.catalogQueries[mocks.catalogQueries.length - 1];
			expect(last.get('task')).toBe('txt2video');
			expect(last.get('enabled')).toBe('true');
			await screenshot(page, JOURNEY, `filtered-${viewport.name}`);

			await page.getByRole('button', { name: 'Clear all' }).first().click();
			await expect(modelRow(page, 'Seedream 4.5')).toBeVisible();
		});

		test('no matches shows a way back', async ({ page }) => {
			await mockCloud(page);
			await openCatalog(page, '&q=zzzz-nothing');
			await expect(page.getByText('No models match your filters')).toBeVisible();
			await screenshot(page, JOURNEY, `no-match-${viewport.name}`);
			await page.getByRole('button', { name: 'Clear filters' }).click();
			await expect(modelRow(page, 'Veo 3.1')).toBeVisible();
		});

		test('a toggle flips at once, sticks on success and rolls back with an inline error on failure', async ({ page }) => {
			const mocks = await mockCloud(page);
			await openCatalog(page);
			const seedream = modelRow(page, 'Seedream 4.5').getByRole('switch');
			await seedream.click();
			await expect(seedream).toBeChecked();
			await expect(page.getByTestId('catalog-summary')).toContainText('2 enabled');
			await expect(page.getByTestId('catalog-summary')).toContainText('3 enabled', { timeout: 5000 });
			expect(mocks.toggles[0]).toEqual({ slugs: ['seedream-4-5'], enable: true });

			const broken = modelRow(page, 'Model That Fails To Enable').getByRole('switch');
			await broken.click();
			await expect(modelRow(page, 'Model That Fails To Enable').getByRole('alert')).toHaveText(
				'The provider refused to enable this model.',
				{ timeout: 5000 }
			);
			await expect(broken).not.toBeChecked();
			await expect(page.getByTestId('catalog-summary')).toContainText('3 enabled');
			await screenshot(page, JOURNEY, `toggle-error-${viewport.name}`);
		});

		test('refresh shows a summary with the skipped models expandable', async ({ page }) => {
			const mocks = await mockCloud(page);
			await openCatalog(page);
			await page.getByRole('button', { name: 'Refresh catalog' }).first().click();
			await expect(page.getByTestId('refresh-summary')).toContainText('66 listed · 3 new · 1 missing from provider · 2 skipped', {
				timeout: 5000
			});
			expect(mocks.refreshes).toBe(1);
			await screenshot(page, JOURNEY, `refresh-summary-${viewport.name}`);
			await page.getByRole('button', { name: 'Show skipped models' }).click();
			await expect(page.getByTestId('skipped-list')).toContainText('odd/bad-price');
			await expect(page.getByTestId('skipped-list')).toContainText("unknown price unit 'furlong'");
			await screenshot(page, JOURNEY, `refresh-skipped-${viewport.name}`);
		});

		test('an empty refresh says nothing was changed and a provider failure shows inline', async ({ page }) => {
			const mocks = await mockCloud(page);
			await openCatalog(page);
			mocks.refreshResponse = {
				status: 200,
				body: {
					success: true,
					data: { ...DEFAULT_REFRESH, empty: true, message: 'The provider returned no models; nothing was changed.', listed: 0, accepted: 0, created: 0, vanished: [], skipped: [], refreshed_at: null }
				}
			};
			await page.getByRole('button', { name: 'Refresh catalog' }).first().click();
			await expect(page.getByTestId('refresh-empty')).toContainText('nothing was changed', { timeout: 5000 });
			await screenshot(page, JOURNEY, `refresh-empty-${viewport.name}`);

			mocks.refreshResponse = {
				status: 502,
				body: { detail: { error: 'cloud_auth', message: 'The provider rejected the API key.' } }
			};
			await page.getByRole('button', { name: 'Refresh catalog' }).first().click();
			await expect(page.getByText('The provider rejected the API key.')).toBeVisible({ timeout: 5000 });
			await screenshot(page, JOURNEY, `refresh-error-${viewport.name}`);
		});

		test('selecting rows offers bulk enable and disable', async ({ page }) => {
			const mocks = await mockCloud(page);
			await openCatalog(page);
			if (viewport.mobile) {
				await expect(page.getByTestId('bulk-bar')).toHaveCount(0);
				return;
			}
			await gridRow(page, 'Seedream 4.5').getByRole('checkbox').click();
			await gridRow(page, 'Filler Model 0').getByRole('checkbox').click();
			await gridRow(page, 'Veo 3.1').getByRole('checkbox').click();
			await expect(page.getByTestId('bulk-bar')).toContainText('3 selected');
			await screenshot(page, JOURNEY, `bulk-selected-${viewport.name}`);
			await page.getByRole('button', { name: 'Enable 2' }).click();
			await expect(gridRow(page, 'Seedream 4.5').getByRole('switch')).toBeChecked();
			await expect(page.getByTestId('bulk-bar')).toHaveCount(0, { timeout: 5000 });
			expect(mocks.toggles[0]).toEqual({ slugs: ['seedream-4-5', 'filler-0'], enable: true });
			await expect(page.getByTestId('catalog-summary')).toContainText('4 enabled');
		});

		test('an empty catalog offers a refresh that fills it', async ({ page }) => {
			const mocks = await mockCloud(page, { entries: [], refreshedAt: null });
			await openCatalog(page);
			await expect(page.getByText('No models yet')).toBeVisible();
			await expect(page.getByTestId('catalog-summary')).toContainText('Never refreshed');
			await screenshot(page, JOURNEY, `empty-${viewport.name}`);
			mocks.entries = fixtureEntries();
			await page.getByRole('button', { name: 'Refresh catalog' }).last().click();
			await expect(modelRow(page, 'Veo 3.1')).toBeVisible({ timeout: 5000 });
		});

		test('a load error offers a retry', async ({ page }) => {
			const mocks = await mockCloud(page);
			mocks.catalogFailure = true;
			await openCatalog(page);
			await expect(page.getByText('Could not load the catalog')).toBeVisible();
			await screenshot(page, JOURNEY, `load-error-${viewport.name}`);
			mocks.catalogFailure = false;
			await page.getByRole('button', { name: 'Retry' }).click();
			await expect(modelRow(page, 'Veo 3.1')).toBeVisible();
		});

		test('the Catalog tab exists only on the cloud backend', async ({ page }) => {
			await mockCloud(page);
			await loginAsOwner(page);
			await page.goto('/admin?tab=backends&backend=local');
			const nav = page.getByRole('navigation', { name: 'Backend details' });
			await expect(nav).toBeVisible({ timeout: 15000 });
			await expect(nav.getByRole('button', { name: 'Catalog' })).toHaveCount(0);
			await page.goto('/admin?tab=backends&backend=cloud1');
			await expect(nav.getByRole('button', { name: 'Catalog' })).toBeVisible({ timeout: 15000 });
		});
	});
}
