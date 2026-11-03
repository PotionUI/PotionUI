import { test, expect, type Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { loginAsOwner, ownerToken, shotPath } from './helpers';
import { assignPreset, startFromNoTabs } from './formSeedMocks';
import { selectPresetInPicker } from './presetPreamble';
import { fixturePng } from './imageEditorHelpers';

const JOURNEY = 'qwen21-combine-output-size';

test.use({ viewport: { width: 1440, height: 900 } });

const SLOT_FILES: Array<[string, string, string]> = [
	['diffusion_model', 'diffusion_models', 'transformer_blocks.0.attn.to_q.weight'],
	['text_encoder', 'text_encoders', 'model.layers.0.self_attn.q_proj.weight'],
	['vae', 'vae', 'decoder.conv_in.weight']
];

function safetensors(key: string): Buffer {
	const bytes = Buffer.from(new Float32Array(16).buffer);
	let json = JSON.stringify({ [key]: { dtype: 'F32', shape: [4, 4], data_offsets: [0, bytes.length] } });
	while ((8 + Buffer.byteLength(json)) % 8 !== 0) json += ' ';
	const length = Buffer.alloc(8);
	length.writeBigUInt64LE(BigInt(Buffer.byteLength(json)));
	return Buffer.concat([length, Buffer.from(json), bytes]);
}

async function installSlotModels(page: Page, presetId: string) {
	const db = process.env.E2E_DB_PATH;
	expect(db, 'run through tests/e2e/ui/run.py so E2E_DB_PATH names the throwaway instance').toBeTruthy();
	const root = join(dirname(dirname(db as string)), 'models');
	for (const [slot, folder, key] of SLOT_FILES) {
		mkdirSync(join(root, folder), { recursive: true });
		writeFileSync(join(root, folder, `e2e_combine_${slot}.safetensors`), safetensors(key));
	}
	const headers = { Authorization: `Bearer ${await ownerToken(page)}` };
	await page.request.post('/api/models/index', { headers, data: {} });
	for (const [slot] of SLOT_FILES) {
		await expect
			.poll(
				async () => {
					const res = await page.request.get(`/api/presets/${presetId}/models?model_type=${slot}&limit=50`, { headers });
					const models = ((await res.json()).data?.models ?? []) as Array<{ filename?: string }>;
					return models.some((m) => (m.filename ?? '').includes(`e2e_combine_${slot}`));
				},
				{ timeout: 60000, intervals: [1000] }
			)
			.toBe(true);
	}
}

async function openCombine(page: Page) {
	await loginAsOwner(page);
	const preset = await assignPreset(page, 'Qwen-Image-2.1');
	test.skip(!preset, 'Qwen-Image-2.1 preset is not available on this instance');
	await installSlotModels(page, preset!.id);
	await page.goto('/generate');
	await startFromNoTabs(page);
	await page.goto('/generate');
	await page.waitForLoadState('networkidle');
	expect(await selectPresetInPicker(page, { id: preset!.id, name: preset!.name })).toBe(true);
	await page.getByTestId('preset-header-mode').locator('button').first().click();
	await page.getByRole('option').filter({ hasText: /Combine images/ }).first().click();
	await expect(page.getByTestId('preset-header-mode')).toContainText('Combine images');
	await page.getByRole('tablist', { name: 'tabs', exact: true }).getByRole('tab', { name: 'References' }).click();
}

async function shootSection(page: Page, theme: 'dark' | 'light', label: string) {
	await page.evaluate((value) => document.documentElement.setAttribute('data-theme', value), theme);
	await page.mouse.move(page.viewportSize()!.width - 2, 2);
	await page.evaluate(() => document.fonts.ready);
	const first = page.locator('[data-field-name="output_size"]');
	const fit = page.locator('[data-field-name="output_fit"]');
	const last = (await fit.count()) > 0 ? fit : first;
	await first.evaluate((el) => el.scrollIntoView({ block: 'center' }));
	const top = (await first.boundingBox())!;
	const bottom = (await last.boundingBox())!;
	await page.screenshot({
		path: shotPath(JOURNEY, `${label}-${theme}`),
		animations: 'disabled',
		clip: { x: top.x - 16, y: top.y - 48, width: top.width + 32, height: bottom.y + bottom.height - top.y + 64 }
	});
}

test('custom output size shows the resolution picker and is submitted', async ({ page }) => {
	test.setTimeout(120000);
	await openCombine(page);

	const outputSize = page.locator('[data-field-name="output_size"]');
	await expect(outputSize).toBeVisible({ timeout: 20000 });
	await expect(outputSize).toContainText('Match first image');
	await expect(page.locator('[data-field-name="resolution"]')).toHaveCount(0);
	await expect(page.locator('[data-field-name="output_fit"]')).toHaveCount(0);
	for (const theme of ['dark', 'light'] as const) await shootSection(page, theme, '01-match');

	const images = page.locator('[data-field-name="images"]').first();
	await images.locator('input[type="file"]').first().setInputFiles({ name: 'e2e-base.png', mimeType: 'image/png', buffer: fixturePng(64) });
	await expect(images.locator('img').first()).toBeVisible({ timeout: 20000 });

	await outputSize.getByRole('button').first().click();
	await page.getByRole('option').filter({ hasText: 'Custom' }).first().click();
	await expect(outputSize).toContainText('Custom');

	const resolution = page.locator('[data-field-name="resolution"]');
	await expect(resolution).toBeVisible();
	await expect(page.locator('[data-field-name="output_fit"]')).toContainText('Crop to fill');
	await resolution.locator('button').first().click();
	await page.getByPlaceholder('Search ratio or size…').fill('16:9');
	const wide = page.getByRole('option').first();
	const picked = ((await wide.innerText()).match(/(\d+)\s*[x×]\s*(\d+)/) ?? []).slice(1, 3).join('x');
	expect(picked).toMatch(/^\d+x\d+$/);
	const [w, h] = picked.split('x').map(Number);
	expect(Math.abs(w / h - 16 / 9)).toBeLessThan(0.02);
	await wide.click();
	await expect(page.getByRole('option')).toHaveCount(0);
	for (const theme of ['dark', 'light'] as const) await shootSection(page, theme, '02-custom-16x9');
	await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'));

	const editor = page.locator('.inline-chip-editor[role="textbox"]').first();
	await editor.click();
	await page.keyboard.type('put the hat on the person');

	const request = page.waitForRequest((r) => r.url().includes('/api/generations/start'), { timeout: 20000 });
	const response = page.waitForResponse((r) => r.url().includes('/api/generations/start'), { timeout: 20000 });
	const generate = page.getByRole('button', { name: 'Generate', exact: true });
	await expect(generate).toBeEnabled({ timeout: 15000 });
	await generate.click();
	const body = (await request).postDataJSON();
	expect(body.mode).toBe('combine');
	expect(body.form_data.output_size).toBe('custom');
	expect(body.form_data.resolution).toBe(picked);
	expect(body.form_data.output_fit).toBe('crop');

	const reply = await response;
	if (!reply.ok()) {
		const errors = ((await reply.json()).field_errors ?? {}) as Record<string, unknown>;
		for (const field of ['output_size', 'resolution', 'output_fit', 'images']) expect(errors[field], field).toBeUndefined();
	}
});
