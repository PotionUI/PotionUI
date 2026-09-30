import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'cloud-capability-fields';
const BEAT = 400;

async function json(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
}

const MODELS = [
	{
		id: 'cloud-a',
		filename: 'cloud-a',
		name: 'Lumen Pro',
		model_type: 'cloud',
		is_available: true,
		is_favorite: false,
		provider_label: 'OpenRouter',
		vendor: 'lumen',
		providers: [{ provider: 'cloud.openrouter' }],
		tags: []
	},
	{
		id: 'cloud-b',
		filename: 'cloud-b',
		name: 'Basic Sketch',
		model_type: 'cloud',
		is_available: true,
		is_favorite: false,
		provider_label: 'OpenRouter',
		vendor: 'lumen',
		providers: [{ provider: 'cloud.openrouter' }],
		tags: []
	}
];

const CAPABILITIES: Record<string, unknown> = {
	'cloud-a': {
		model_id: 'cloud-a',
		slug: 'lumen-pro',
		label: 'Lumen Pro',
		vendor: 'lumen',
		tasks: ['txt2img'],
		outputs: ['image'],
		max_outputs_per_job: 1,
		deprecated: false,
		params: [
			{ name: 'aspect_ratio', kind: 'enum', values: ['1:1', '16:9', '9:16'], default: '1:1', label: 'Aspect ratio', tasks: [] },
			{ name: 'guidance', kind: 'range', minimum: 1, maximum: 10, step: 0.5, default: 5, label: 'Guidance', tasks: [] },
			{ name: 'x.style', kind: 'enum', values: ['cinematic', 'noir', 'watercolor'], extra: true, label: 'Style', description: 'Visual look applied by the provider.', tasks: [] },
			{ name: 'x.hdr', kind: 'boolean', extra: true, label: 'HDR', description: 'Render in high dynamic range.', tasks: [] },
			{ name: 'x.note', kind: 'text', extra: true, label: 'Director note', tasks: [] }
		],
		inputs: [{ role: 'reference', modality: 'image', min_items: 0, max_items: 3, tasks: [] }]
	},
	'cloud-b': {
		model_id: 'cloud-b',
		slug: 'basic-sketch',
		label: 'Basic Sketch',
		vendor: 'sketch',
		tasks: ['txt2img'],
		outputs: ['image'],
		max_outputs_per_job: 1,
		deprecated: false,
		params: [{ name: 'guidance', kind: 'range', minimum: 0, maximum: 3, step: 1, integer: true, default: 1, label: 'Guidance', tasks: [] }],
		inputs: []
	}
};

const FORM_SCHEMA = {
	properties: {
		generation: {
			type: 'group',
			title: 'Generation',
			children: [
				{
					name: 'model',
					type: 'model',
					title: 'Model',
					default: 'model:cloud-a',
					configuration: { model_type: 'cloud', tasks: ['txt2img'] }
				},
				{
					name: 'prompt',
					type: 'string',
					title: 'Prompt',
					default: 'a lighthouse at dusk'
				},
				{
					name: 'aspect_ratio',
					type: 'select',
					title: 'Aspect ratio',
					default: '1:1',
					options: [
						{ label: 'Square', value: '1:1' },
						{ label: 'Wide', value: '16:9' },
						{ label: 'Tall', value: '9:16' },
						{ label: 'Ultra wide', value: '21:9' }
					],
					capability: { model_field: 'model', param: 'aspect_ratio' }
				},
				{
					name: 'guidance',
					type: 'slider',
					title: 'Guidance',
					default: 5,
					minimum: 0,
					maximum: 100,
					step: 1,
					capability: { model_field: 'model', param: 'guidance' }
				},
				{
					name: 'references',
					type: 'string',
					title: 'Reference image path',
					capability: { model_field: 'model', input: 'reference' }
				},
				{
					name: 'provider_options',
					type: 'cloud_options',
					title: 'Provider options',
					capability: { model_field: 'model' },
					configuration: { include_unbound: false }
				}
			]
		}
	}
};

async function apiGet(page: Page, url: string, token: string) {
	const res = await page.request.get(url, { headers: { Authorization: `Bearer ${token}` } });
	expect(res.ok(), `GET ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

async function apiPost(page: Page, url: string, token: string, data?: unknown) {
	const res = await page.request.post(url, { headers: { Authorization: `Bearer ${token}` }, data: data ?? {} });
	expect(res.ok(), `POST ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

async function mockCloud(page: Page) {
	const capabilityCalls: string[] = [];
	await page.route('**/api/presets/*/form*', (route) => json(route, { success: true, data: { preset_id: 'mock', form_schema: FORM_SCHEMA } }));
	await page.route('**/api/cloud/models/*/capabilities*', (route) => {
		const id = new URL(route.request().url()).pathname.split('/').slice(-2)[0];
		capabilityCalls.push(id);
		const body = CAPABILITIES[id];
		if (!body) return json(route, { success: false, error: 'model_not_found' }, 404);
		return json(route, { success: true, data: body });
	});
	await page.route(
		(url) => /^\/api\/(models|presets\/[^/]+\/models)(\/|$)/.test(url.pathname),
		async (route) => {
			const url = new URL(route.request().url());
			const single = url.pathname.match(/^\/api\/models\/([^/]+)$/);
			if (single && route.request().method() === 'GET') {
				const model = MODELS.find((m) => m.id === single[1]);
				return model ? json(route, { success: true, data: { model } }) : json(route, { success: false }, 404);
			}
			if (route.request().method() === 'GET' && (url.pathname === '/api/models' || url.pathname.endsWith('/models'))) {
				return json(route, {
					success: true,
					data: { engine: 'native', models: MODELS, total: MODELS.length, indexed: true, availability_indexed: true }
				});
			}
			return route.fallback();
		}
	);
	return { capabilityCalls };
}

async function settledSheet(page: Page) {
	const sheet = page.getByRole('dialog', { name: 'Settings' });
	let previous = '';
	for (let attempt = 0; attempt < 40; attempt++) {
		const box = await sheet.boundingBox();
		const current = JSON.stringify(box);
		if (current === previous) return;
		previous = current;
		await page.waitForTimeout(150);
	}
}

async function openCloudForm(page: Page, mobile: boolean) {
	await loginAsOwner(page);
	const token = await ownerToken(page);
	const me = await apiGet(page, '/api/auth/me', token);
	const list = await apiGet(page, '/api/presets?include_uninstalled=true', token);
	const presets = (list.data || []) as Array<{ id: string; name: string; engine?: string; installed?: boolean }>;
	const preset = presets.find((p) => p.engine === 'native');
	if (!preset) return null;
	if (!preset.installed) await apiPost(page, `/api/presets/${preset.id}/install`, token);
	await apiPost(page, `/api/presets/${preset.id}/assign`, token, { user_ids: [me.data.id] });
	await page.goto('/generate');
	if (mobile) {
		await page.getByRole('button', { name: 'Open preset and session' }).click();
		const presetSheet = page.getByRole('dialog', { name: 'Preset and session' });
		await expect(presetSheet).toBeVisible({ timeout: 5000 });
		const trigger = presetSheet.locator('button[aria-haspopup="dialog"]', { hasText: 'Choose a preset' });
		const needsPreset = await trigger.waitFor({ state: 'visible', timeout: 8000 }).then(() => true, () => false);
		if (needsPreset) {
			await trigger.click();
			await page.locator('[role="listbox"][aria-label="Presets"]').getByText(preset.name, { exact: true }).click();
			await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
		}
		await page.keyboard.press('Escape');
		await expect(presetSheet).toHaveCount(0);
		await page.getByRole('button', { name: 'Settings', exact: true }).click();
		await expect(page.getByRole('dialog', { name: 'Settings' })).toBeVisible({ timeout: 5000 });
		await settledSheet(page);
		return preset;
	}
	const choose = page.getByRole('button', { name: 'Choose a preset' });
	const needsPreset = await choose.waitFor({ state: 'visible', timeout: 8000 }).then(() => true, () => false);
	if (needsPreset) {
		await choose.click();
		await page.getByText(preset.name, { exact: true }).first().click();
		await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
	}
	await page.waitForTimeout(BEAT);
	return preset;
}

const field = (page: Page, name: string) => page.locator(`[data-field-name="${name}"]`);

async function chooseModel(page: Page, label: string) {
	await field(page, 'model').locator('button:visible', { hasText: 'Swap' }).first().click();
	await field(page, 'model').locator('input').first().click();
	await page.getByText(label, { exact: true }).last().click();
	await page.waitForTimeout(BEAT);
}

for (const viewport of [
	{ name: '1440', width: 1440, height: 900 },
	{ name: '390', width: 390, height: 844 }
]) {
	test.describe(`cloud capability fields @${viewport.name}`, () => {
		test.use({ viewport: { width: viewport.width, height: viewport.height } });

		test('the form follows the chosen model', async ({ page }) => {
			test.setTimeout(120000);
			const mocks = await mockCloud(page);
			const preset = await openCloudForm(page, viewport.width < 600);
			test.skip(!preset, 'No native preset available on this throwaway instance.');

			await expect(field(page, 'aspect_ratio')).toBeVisible({ timeout: 20000 });
			await expect(field(page, 'provider_options')).toContainText('Director note');
			await expect(field(page, 'references')).toBeVisible();
			expect(mocks.capabilityCalls).toContain('cloud-a');
			const slider = field(page, 'guidance').locator('input[type="range"]');
			await expect(slider).toHaveAttribute('max', '10');
			await expect(slider).toHaveAttribute('step', '0.5');
			await screenshot(page, JOURNEY, `lumen-pro-${viewport.name}`);

			await field(page, 'aspect_ratio').locator('button[aria-haspopup="listbox"]').click();
			await expect(page.getByRole('option', { name: 'Ultra wide' })).toHaveCount(0);
			await expect(page.getByRole('option', { name: 'Tall' })).toBeVisible();
			await screenshot(page, JOURNEY, `lumen-pro-aspect-open-${viewport.name}`);
			await page.getByRole('option', { name: 'Square' }).click();

			await chooseModel(page, 'Basic Sketch');
			await expect(field(page, 'aspect_ratio')).toHaveCount(0);
			await expect(field(page, 'references')).toHaveCount(0);
			await expect(field(page, 'provider_options')).toHaveCount(0);
			await expect(slider).toHaveAttribute('max', '3');
			await expect(slider).toHaveAttribute('step', '1');
			await screenshot(page, JOURNEY, `basic-sketch-${viewport.name}`);

			await chooseModel(page, 'Lumen Pro');
			await expect(field(page, 'aspect_ratio')).toBeVisible();
			await expect(field(page, 'provider_options')).toContainText('HDR');
		});

		test('a request error leaves the fields as declared', async ({ page }) => {
			test.setTimeout(120000);
			await mockCloud(page);
			await page.route('**/api/cloud/models/*/capabilities*', (route) => json(route, { success: false, error: 'model_not_found' }, 404));
			const preset = await openCloudForm(page, viewport.width < 600);
			test.skip(!preset, 'No native preset available on this throwaway instance.');

			await expect(field(page, 'aspect_ratio')).toBeVisible({ timeout: 20000 });
			await expect(field(page, 'references')).toBeVisible();
			await expect(field(page, 'provider_options')).toHaveCount(0);
			await screenshot(page, JOURNEY, `unknown-capabilities-${viewport.name}`);
		});
	});
}

test.describe('settings sheet reference @390', () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test('the sheet of a native preset without any cloud field', async ({ page }) => {
		test.setTimeout(120000);
		const preset = await openCloudForm(page, true);
		test.skip(!preset, 'No native preset available on this throwaway instance.');
		await screenshot(page, JOURNEY, 'native-settings-sheet-390');
	});
});
