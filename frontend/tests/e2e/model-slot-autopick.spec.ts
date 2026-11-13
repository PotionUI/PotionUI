import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { presetOption } from './presetPreamble';

const JOURNEY = 'model-slot-autopick';
const SLOTS = ['diffusion_model', 'text_encoder', 'vae'] as const;

async function json(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
}

function modelRow(id: string, filename: string, modelType: string) {
	const now = new Date().toISOString();
	return {
		id,
		filename,
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

const SINGLE: Record<string, any[]> = {
	diffusion_model: [modelRow('dm-1', 'flux3-dev-bf16.safetensors', 'diffusion_model')],
	text_encoder: [modelRow('te-1', 'qwen3vl_4b_bf16.safetensors', 'text_encoder')],
	vae: [modelRow('vae-1', 'flux3-vae.safetensors', 'vae')]
};

const DOUBLE: Record<string, any[]> = {
	diffusion_model: [
		modelRow('dm-1', 'flux3-dev-bf16.safetensors', 'diffusion_model'),
		modelRow('dm-2', 'flux3-dev-fp8.safetensors', 'diffusion_model')
	],
	text_encoder: [
		modelRow('te-1', 'qwen3vl_4b_bf16.safetensors', 'text_encoder'),
		modelRow('te-2', 'qwen3vl_4b_fp8_scaled.safetensors', 'text_encoder')
	],
	vae: [
		modelRow('vae-1', 'flux3-vae.safetensors', 'vae'),
		modelRow('vae-2', 'flux3-vae-alt.safetensors', 'vae')
	]
};

function modelField(name: string, title: string, modelType: string, required = true) {
	return {
		name,
		type: 'model',
		title,
		required,
		default: '',
		configuration: { model_type: modelType }
	};
}

const FORM_SCHEMA = {
	properties: {
		generation: {
			type: 'group',
			title: 'Generation',
			children: [
				modelField('diffusion_model', 'Diffusion Model', 'diffusion_model'),
				modelField('text_encoder', 'Text Encoder', 'text_encoder'),
				modelField('vae', 'VAE', 'vae'),
				{ name: 'prompt', type: 'string', title: 'Prompt', default: 'a lighthouse at dusk' }
			]
		}
	}
};

async function mockForm(page: Page, rows: Record<string, any[]>) {
	const requests: Record<string, number> = {};
	await page.route('**/api/presets/*/form*', (route) =>
		json(route, { success: true, data: { preset_id: 'mock', form_schema: FORM_SCHEMA } })
	);
	await page.route(
		(url) => /^\/api\/(models|presets\/[^/]+\/models)(\/|$)/.test(url.pathname),
		async (route) => {
			const url = new URL(route.request().url());
			if (route.request().method() !== 'GET') return route.fallback();
			const single = url.pathname.match(/^\/api\/models\/([^/]+)$/);
			if (single) {
				const found = Object.values(rows).flat().find((m) => m.id === single[1]);
				return found ? json(route, { success: true, data: { model: found } }) : json(route, { success: false }, 404);
			}
			if (!url.pathname.endsWith('/models')) return route.fallback();
			const type = url.searchParams.get('model_type') ?? '';
			requests[type] = (requests[type] ?? 0) + 1;
			const limit = Number(url.searchParams.get('limit') || 100);
			const all = rows[type] ?? [];
			return json(route, {
				success: true,
				data: { engine: 'native', models: all.slice(0, limit), total: all.length, indexed: true, availability_indexed: true }
			});
		}
	);
	return requests;
}

async function openForm(page: Page, mobile: boolean) {
	await loginAsOwner(page);
	const token = await ownerToken(page);
	const headers = { Authorization: `Bearer ${token}` };
	const me = await (await page.request.get('/api/auth/me', { headers })).json();
	const list = await (await page.request.get('/api/presets?include_uninstalled=true', { headers })).json();
	const presets = (list.data || []) as Array<{ id: string; name: string; engine?: string; installed?: boolean }>;
	const preset = presets.find((p) => p.engine === 'native');
	if (!preset) return null;
	if (!preset.installed) await page.request.post(`/api/presets/${preset.id}/install`, { headers, data: {} });
	await page.request.post(`/api/presets/${preset.id}/assign`, { headers, data: { user_ids: [me.data.id] } });
	await page.goto('/generate');
	const choose = page.getByRole('button', { name: 'Choose a preset' });
	if (mobile) {
		await page.getByRole('button', { name: 'Open preset and session' }).click();
		const presetSheet = page.getByRole('dialog', { name: 'Preset and session' });
		await expect(presetSheet).toBeVisible({ timeout: 5000 });
		const trigger = presetSheet.locator('button[aria-haspopup="dialog"]', { hasText: 'Choose a preset' });
		if (await trigger.waitFor({ state: 'visible', timeout: 8000 }).then(() => true, () => false)) {
			await trigger.click();
			await presetOption(page, preset.name).click();
			await expect(page.getByRole('dialog', { name: 'Choose preset' })).toBeHidden();
		}
		await page.keyboard.press('Escape');
		await expect(presetSheet).toHaveCount(0);
		await page.getByRole('button', { name: 'Settings', exact: true }).click();
		await expect(page.getByRole('dialog', { name: 'Settings' })).toBeVisible({ timeout: 5000 });
		return preset;
	}
	if (await choose.waitFor({ state: 'visible', timeout: 8000 }).then(() => true, () => false)) {
		await choose.click();
		await presetOption(page, preset.name).click();
		await expect(page.getByRole('dialog', { name: 'Choose preset' })).toBeHidden();
	}
	return preset;
}

async function fillPrompt(page: Page, mobile: boolean) {
	if (mobile) {
		await page.keyboard.press('Escape');
		await expect(page.getByRole('dialog', { name: 'Settings' })).toHaveCount(0);
		await page.getByRole('button', { name: 'Prompt', exact: true }).click();
	}
	const editor = page.locator('.inline-chip-editor[role="textbox"]').first();
	await expect(editor).toBeVisible({ timeout: 20000 });
	await editor.click();
	await page.keyboard.type('a lighthouse at dusk');
	if (mobile) {
		await page.keyboard.press('Escape');
		await expect(page.getByRole('dialog', { name: 'Prompt', exact: true })).toHaveCount(0);
	}
}

const field = (page: Page, name: string) => page.locator(`[data-field-name="${name}"]`);

for (const viewport of [
	{ name: '1440', width: 1440, height: 900 },
	{ name: '390', width: 390, height: 844 }
]) {
	test.describe(`model slot autopick @${viewport.name}`, () => {
		test.use({ viewport: { width: viewport.width, height: viewport.height } });

		test('one candidate per slot fills all three and enables Generate', async ({ page }) => {
			test.setTimeout(120000);
			const requests = await mockForm(page, SINGLE);
			const preset = await openForm(page, viewport.width < 600);
			expect(preset, 'a native preset must exist on the throwaway instance to host the mocked form').not.toBeNull();

			await expect(field(page, 'diffusion_model')).toBeVisible({ timeout: 20000 });
			await expect(field(page, 'diffusion_model')).toContainText('flux3-dev-bf16', { timeout: 10000 });
			await expect(field(page, 'text_encoder')).toContainText('qwen3vl_4b_bf16', { timeout: 10000 });
			await expect(field(page, 'vae')).toContainText('flux3-vae', { timeout: 10000 });
			await fillPrompt(page, viewport.width < 600);
			await expect(page.getByRole('button', { name: 'Generate', exact: true })).toBeEnabled({ timeout: 10000 });
			for (const slot of SLOTS) expect(requests[slot], `${slot} slot requests`).toBeLessThanOrEqual(3);
			await screenshot(page, JOURNEY, `single-candidates-${viewport.name}`);
		});

		test('a slot the user cleared stays empty until a reload, which fills a saved empty slot with the one model that fits', async ({ page }) => {
			test.setTimeout(120000);
			await mockForm(page, SINGLE);
			const preset = await openForm(page, viewport.width < 600);
			expect(preset, 'a native preset must exist on the throwaway instance to host the mocked form').not.toBeNull();

			await expect(field(page, 'vae')).toContainText('flux3-vae', { timeout: 10000 });
			await field(page, 'vae').getByRole('button', { name: 'Swap', exact: true }).click();
			await expect(field(page, 'vae').locator('input').first()).toHaveValue('');
			await page.waitForTimeout(1500);
			await expect(field(page, 'vae')).not.toContainText('flux3-vae');
			await page.reload();
			if (viewport.width < 600) {
				await page.getByRole('button', { name: 'Settings', exact: true }).click();
			}
			await expect(field(page, 'diffusion_model')).toBeVisible({ timeout: 20000 });
			await expect(field(page, 'diffusion_model')).toContainText('flux3-dev-bf16', { timeout: 10000 });
			await expect(field(page, 'vae')).toContainText('flux3-vae', { timeout: 10000 });
			await screenshot(page, JOURNEY, `cleared-stays-empty-${viewport.name}`);
		});

		test('two candidates leave every slot empty', async ({ page }) => {
			test.setTimeout(120000);
			await mockForm(page, DOUBLE);
			const preset = await openForm(page, viewport.width < 600);
			expect(preset, 'a native preset must exist on the throwaway instance to host the mocked form').not.toBeNull();

			await expect(field(page, 'diffusion_model')).toBeVisible({ timeout: 20000 });
			await page.waitForTimeout(1500);
			for (const slot of SLOTS) {
				await expect(field(page, slot)).not.toContainText('.safetensors');
				await expect(field(page, slot).locator('input').first()).toHaveValue('');
			}
			await screenshot(page, JOURNEY, `two-candidates-${viewport.name}`);
		});
	});
}
