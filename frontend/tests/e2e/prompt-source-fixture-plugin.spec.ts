import { test, expect, type Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { apiGet, apiPost, loginAsOwner, ownerToken, screenshot } from './helpers';
import { assignPreset, startFromNoTabs } from './formSeedMocks';
import { selectPresetInPicker } from './presetPreamble';

const JOURNEY = 'prompt-source-fixture-plugin';
const PLUGIN_ID = 'e2e-preset-extension';
const SOURCE = 'fixture-bundle';
const KREA2_ID = '4TK1KBQZ2XMB8ME0PTMXS1YJQP';
const STEM = 'e2e_stack_bundle';

function loraFile(): Buffer {
	const names = ['lora_unet_down_blocks_0_attentions_0_proj_in.lora_down.weight', 'lora_unet_down_blocks_0_attentions_0_proj_in.lora_up.weight'];
	const header: Record<string, unknown> = { __metadata__: { ss_network_module: 'networks.lora' } };
	const blobs: Buffer[] = [];
	let offset = 0;
	for (const name of names) {
		const bytes = Buffer.from(new Float32Array(16).fill(0.01).buffer);
		header[name] = { dtype: 'F32', shape: [4, 4], data_offsets: [offset, offset + bytes.length] };
		offset += bytes.length;
		blobs.push(bytes);
	}
	let json = JSON.stringify(header);
	while ((8 + Buffer.byteLength(json)) % 8 !== 0) json += ' ';
	const length = Buffer.alloc(8);
	length.writeBigUInt64LE(BigInt(Buffer.byteLength(json)));
	return Buffer.concat([length, Buffer.from(json), ...blobs]);
}

async function indexLora(page: Page, token: string): Promise<string> {
	const db = process.env.E2E_DB_PATH;
	expect(db, 'run through tests/e2e/ui/run.py so E2E_DB_PATH names the throwaway instance').toBeTruthy();
	const dir = join(dirname(dirname(db as string)), 'models', 'loras');
	mkdirSync(dir, { recursive: true });
	writeFileSync(join(dir, `${STEM}.safetensors`), loraFile());
	await apiPost(page, '/api/models/index', token, {});
	let id = '';
	await expect
		.poll(
			async () => {
				const list = await apiGet(page, '/api/models?model_type=lora&limit=50&all_models=true', token);
				const found = ((list.data?.models ?? []) as Array<{ id: string; filename?: string; name?: string }>).find((m) =>
					(m.filename ?? m.name ?? '').includes(STEM)
				);
				id = found?.id ?? '';
				return id;
			},
			{ timeout: 60000, intervals: [1000] }
		)
		.not.toBe('');
	return id;
}

test.describe('a plugin prompt resource source (fixture plugin, no mocks)', () => {
	test('is served with its badge, answers the generic members route, and is citable from the @ menu', async ({ page }) => {
		test.setTimeout(180000);
		await page.setViewportSize({ width: 1440, height: 960 });
		await loginAsOwner(page);
		const token = await ownerToken(page);
		const modelId = await indexLora(page, token);
		await apiPost(page, '/api/plugins/scan', token);
		await apiPost(page, `/api/plugins/${PLUGIN_ID}/enable`, token);
		try {
			const form = await apiGet(page, `/api/presets/${KREA2_ID}/form?mode=txt2img`, token);
			expect(form.data.prompt_resources).toContainEqual(
				expect.objectContaining({ field: 'loras', source: SOURCE, label: 'Stacks', source_label: 'Bundle', source_badge: 'Kit' })
			);
			const members = await apiGet(page, `/api/prompt-resources/sources/${SOURCE}/members?item=model:${modelId}`, token);
			expect(members.data).toEqual({ members: [] });

			const preset = await assignPreset(page, 'Krea-2');
			expect(preset).toBeTruthy();
			await page.goto('/generate');
			await startFromNoTabs(page);
			await page.goto('/generate');
			await page.waitForLoadState('networkidle');
			expect(await selectPresetInPicker(page, { id: preset!.id, name: preset!.name })).toBe(true);

			const formTabs = page.getByRole('tablist', { name: 'tabs', exact: true });
			await formTabs.getByRole('tab', { name: 'LoRA' }).click();
			const loras = page.locator('[data-field-name="loras"]').first();
			await loras.getByRole('button', { name: /Add LoRA|Add|Choose/ }).first().click();
			await page.getByText(STEM, { exact: false }).first().click();
			await page.keyboard.press('Escape');
			await expect(loras).toContainText(STEM);
			await formTabs.getByRole('tab', { name: 'Generation' }).click();

			await page.locator('[contenteditable="true"]').first().click();
			await page.keyboard.type('a lighthouse with @');
			const picker = page.locator('.resource-picker');
			await expect(picker).toBeVisible();
			const group = picker.locator('.picker-row').filter({ hasText: 'Stacks' }).first();
			await expect(group).toContainText('1 available');
			await group.click();
			const entry = picker.locator('.picker-row').filter({ hasText: STEM }).first();
			await expect(entry).toContainText('Kit');
			await screenshot(page, JOURNEY, 'source-badge-in-at-menu');
			await entry.click();
			await expect(picker).toBeHidden();
			await expect(page.locator('.resource-chip').filter({ hasText: STEM })).toHaveCount(1);
			await screenshot(page, JOURNEY, 'source-item-cited');
		} finally {
			await apiPost(page, `/api/plugins/${PLUGIN_ID}/disable`, token);
		}

		const after = await apiGet(page, `/api/presets/${KREA2_ID}/form?mode=txt2img`, token);
		expect((after.data.prompt_resources as Array<{ source?: string }>).some((entry) => entry.source === SOURCE)).toBe(false);
	});
});
