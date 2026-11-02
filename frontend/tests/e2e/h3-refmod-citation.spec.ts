import { test, expect, type Locator, type Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { assignPreset, startFromNoTabs } from './formSeedMocks';
import { selectPresetInPicker } from './presetPreamble';
import { fixturePng } from './imageEditorHelpers';

const JOURNEY = 'h3-refmod-citation';
const HINT = 'This shot uses no references. Type @ to add one.';
const PICTURE = 'e2e_face_picture';
const BUNDLE = 'e2e_cast_pictures';
const STACK = 'e2e_stack_pictures';

type Tensor = { name: string; shape: number[] };

function safetensors(meta: Record<string, unknown>, tensors: Tensor[]): Buffer {
	const header: Record<string, unknown> = { __metadata__: { refmod_meta: JSON.stringify(meta) } };
	let offset = 0;
	const blobs: Buffer[] = [];
	tensors.forEach((tensor, index) => {
		const count = tensor.shape.reduce((a, b) => a * b, 1);
		const bytes = Buffer.from(new Float32Array(count).map((_, i) => Math.sin(i + index * 7)).buffer);
		header[tensor.name] = { dtype: 'F32', shape: tensor.shape, data_offsets: [offset, offset + bytes.length] };
		offset += bytes.length;
		blobs.push(bytes);
	});
	let json = JSON.stringify(header);
	while ((8 + Buffer.byteLength(json)) % 8 !== 0) json += ' ';
	const length = Buffer.alloc(8);
	length.writeBigUInt64LE(BigInt(Buffer.byteLength(json)));
	return Buffer.concat([length, Buffer.from(json), ...blobs]);
}

function writeRefMods() {
	const db = process.env.E2E_DB_PATH;
	expect(db, 'run through tests/e2e/ui/run.py so E2E_DB_PATH names the throwaway instance').toBeTruthy();
	const dir = join(dirname(dirname(db as string)), 'models', 'refmods');
	mkdirSync(dir, { recursive: true });
	writeFileSync(
		join(dir, `${PICTURE}.safetensors`),
		safetensors({ kind: 'image', name: 'e2e face picture', source: 'image', latent_t: 1, latent_h: 4, latent_w: 4, mode: 'encode' }, [
			{ name: 'latent', shape: [1, 24, 1, 4, 4] }
		])
	);
	writeFileSync(
		join(dir, `${BUNDLE}.safetensors`),
		safetensors(
			{
				_format_version: 5,
				kind: 'bundle',
				name: 'e2e cast pictures',
				members: [
					{ _format_version: 4, kind: 'image', name: 'hero', source: 'image' },
					{ _format_version: 4, kind: 'image', name: 'villain', source: 'image' }
				]
			},
			[
				{ name: 'ref_0', shape: [1, 24, 1, 4, 4] },
				{ name: 'ref_1', shape: [1, 24, 1, 2, 4] }
			]
		)
	);
	writeFileSync(
		join(dir, `${STACK}.safetensors`),
		safetensors({ kind: 'video', name: 'e2e stack pictures', source: 'stack', tags: '3 img, 0 vid', latent_t: 3, latent_h: 4, latent_w: 4, mode: 'encode' }, [
			{ name: 'latent', shape: [1, 24, 3, 4, 4] }
		])
	);
}

async function indexRefMods(page: Page) {
	const headers = { Authorization: `Bearer ${await ownerToken(page)}` };
	await page.request.post('/api/models/index', { headers, data: {} });
	await expect
		.poll(
			async () => {
				const list = await (await page.request.get('/api/models?model_type=refmod&limit=50&all_models=true', { headers })).json();
				const names = ((list.data?.models ?? []) as Array<{ filename?: string; name?: string }>).map((m) => m.filename ?? m.name ?? '');
				return [PICTURE, BUNDLE, STACK].every((stem) => names.some((name) => name.includes(stem)));
			},
			{ timeout: 60000, intervals: [1000] }
		)
		.toBe(true);
}

async function openRefsMode(page: Page) {
	await loginAsOwner(page);
	writeRefMods();
	await indexRefMods(page);
	const preset = await assignPreset(page, 'MiniMax-H3');
	test.skip(!preset, 'MiniMax-H3 preset is not available on this instance');
	await page.goto('/generate');
	await startFromNoTabs(page);
	await page.goto('/generate');
	await page.waitForLoadState('networkidle');
	expect(await selectPresetInPicker(page, { id: preset!.id, name: preset!.name })).toBe(true);
	await page.getByTestId('preset-header-mode').locator('button').first().click();
	await page.getByRole('option').filter({ hasText: /References/ }).first().click();
	await expect(page.getByTestId('preset-header-mode')).toContainText('References');
}

async function fillReferences(page: Page, { native, refMods }: { native: boolean; refMods: string[] }) {
	const formTabs = page.getByRole('tablist', { name: 'tabs', exact: true });
	await formTabs.getByRole('tab', { name: 'References' }).click();
	if (native) {
		const references = page.locator('[data-field-name="references"]').first();
		await references.locator('input[type="file"]').first().setInputFiles({ name: 'e2e-native.png', mimeType: 'image/png', buffer: fixturePng(64) });
		await expect(references.locator('img').first()).toBeVisible({ timeout: 20000 });
	}
	const field = page.locator('[data-field-name="reference_mods"]').first();
	for (const stem of refMods) {
		await field.scrollIntoViewIfNeeded();
		await field.getByRole('button', { name: /Choose RefMods|Add LoRA|Add/ }).first().click();
		await page.getByText(stem, { exact: false }).first().click();
		await page.keyboard.press('Escape');
		await expect(field).toContainText(stem);
	}
	await formTabs.getByRole('tab', { name: 'Generation' }).click();
}

function director(page: Page): Locator {
	return page.locator('section.video-director[aria-label="Video Director"]');
}

async function cite(page: Page, group: string, entry: string) {
	await page.keyboard.type(' @');
	const picker = page.locator('.resource-picker');
	await expect(picker).toBeVisible();
	await picker.locator('.picker-row').filter({ hasText: group }).first().click();
	await picker.locator('.picker-row').filter({ hasText: entry }).first().click();
	await expect(picker).toBeHidden();
}

async function writeShot(page: Page, index: number, text: string, citations: Array<[string, string]>) {
	if (index > 0) {
		await director(page).getByRole('tab', { name: 'Prompt' }).first().click().catch(() => undefined);
		await director(page).locator('[role="button"][aria-label^="Expand "]').last().click();
	}
	await director(page).locator('.stage [contenteditable="true"]').first().click();
	await page.keyboard.type(text);
	for (const [group, entry] of citations) await cite(page, group, entry);
}

async function openReferencesTab(page: Page): Promise<Locator> {
	const tab = director(page).getByRole('tablist', { name: /stage tabs/ }).getByRole('tab', { name: /References/ });
	await tab.click();
	return tab;
}

async function usedTile(page: Page, name: string): Promise<Locator> {
	const tile = director(page).getByTestId('shot-reference-used').filter({ hasText: name });
	await expect(tile).toHaveCount(1);
	return tile;
}

async function capturedSubmission(page: Page): Promise<Record<string, any>> {
	const request = page.waitForRequest((r) => r.url().includes('/api/generations/start'), { timeout: 20000 });
	await page.getByRole('button', { name: /^Generate/ }).last().click();
	const body = (await request).postDataJSON();
	expect(body.form_data.video_director.mode).toBe('director');
	for (const field of ['model', 'text_encoder', 'video_vae', 'audio_vae']) {
		if (!body.form_data[field]) body.form_data[field] = `/models/e2e_${field}.safetensors`;
	}
	return body;
}

async function start(page: Page, body: Record<string, any>) {
	return page.request.post('/api/generations/start', { headers: { Authorization: `Bearer ${await ownerToken(page)}` }, data: body });
}

async function submittedReferenceIndices(page: Page): Promise<Array<number[] | null>> {
	const response = await start(page, await capturedSubmission(page));
	const text = await response.text();
	expect(response.status(), text).toBe(200);
	const id = JSON.parse(text).data.generation_id as string;
	const stored = await (
		await page.request.get(`/api/generations/history/${id}`, { headers: { Authorization: `Bearer ${await ownerToken(page)}` } })
	).json();
	const formData = stored.data?.form_data ?? stored.data?.generation?.form_data;
	return (formData.video_director.segments as Array<{ reference_indices: number[] | null }>).map(
		(segment) => segment.reference_indices
	);
}

test.describe('MiniMax-H3 Director RefMod citation (real RefMod files, no mocks)', () => {
	test.beforeEach(async ({ page }) => {
		test.setTimeout(180000);
		await page.setViewportSize({ width: 1440, height: 960 });
		await openRefsMode(page);
	});

	test('a cited picture RefMod is a picked Picture 1 of 1 and its row reaches the shot', async ({ page }) => {
		await fillReferences(page, { native: false, refMods: [PICTURE] });
		await director(page).getByRole('button', { name: '+ Add shot' }).click();
		await expect(director(page).getByTestId('shot-no-references-hint')).toHaveText(HINT);
		await writeShot(page, 0, 'a lighthouse with', [['RefMods', PICTURE]]);
		await expect(director(page).getByTestId('shot-no-references-hint')).toHaveCount(0);

		const tab = await openReferencesTab(page);
		await expect(tab).toContainText('1 of 1');
		await expect(await usedTile(page, PICTURE)).toContainText('Picture 1');
		await expect(director(page).getByTestId('shot-references')).toContainText('1 of 1');
		await screenshot(page, JOURNEY, 'picture-refmod-cited');

		await writeShot(page, 1, 'the sea at night', []);
		expect(await submittedReferenceIndices(page)).toEqual([[0], []]);
	});

	test('a native picture and a picture RefMod cited together read 2 of 2 and both reach the shot', async ({ page }) => {
		await fillReferences(page, { native: true, refMods: [PICTURE] });
		await director(page).getByRole('button', { name: '+ Add shot' }).click();
		await writeShot(page, 0, 'the keeper', [['Pictures', 'e2e-native'], ['RefMods', PICTURE]]);

		const tab = await openReferencesTab(page);
		await expect(tab).toContainText('2 of 2');
		await expect(await usedTile(page, 'e2e-native')).toContainText('Picture 1');
		await expect(await usedTile(page, PICTURE)).toContainText('Picture 2');
		await screenshot(page, JOURNEY, 'native-and-refmod-cited');

		await writeShot(page, 1, 'the sea at night', []);
		expect(await submittedReferenceIndices(page)).toEqual([[0, 1], []]);
	});

	test('a picture bundle counts every member and a stacked-pictures RefMod is a Video', async ({ page }) => {
		await fillReferences(page, { native: true, refMods: [BUNDLE, STACK] });
		await director(page).getByRole('button', { name: '+ Add shot' }).click();
		await writeShot(page, 0, 'the cast', [['RefMods', BUNDLE]]);

		const first = await openReferencesTab(page);
		await expect(first).toContainText('2 of 4');
		await expect(await usedTile(page, BUNDLE)).toContainText('Picture 1 · Picture 2');
		await screenshot(page, JOURNEY, 'bundle-cited');

		await writeShot(page, 1, 'the montage', [['RefMods', STACK]]);
		const second = await openReferencesTab(page);
		await expect(second).toContainText('1 of 4');
		await expect(await usedTile(page, STACK)).toContainText('Video 1');
		await screenshot(page, JOURNEY, 'stack-cited');

		expect(await submittedReferenceIndices(page)).toEqual([[1, 2], [3]]);
	});

	test('a shot citing a RefMod the form no longer carries is refused by name, never run without it', async ({ page }) => {
		await fillReferences(page, { native: true, refMods: [PICTURE] });
		await director(page).getByRole('button', { name: '+ Add shot' }).click();
		await writeShot(page, 0, 'the keeper', [['Pictures', 'e2e-native'], ['RefMods', PICTURE]]);
		await writeShot(page, 1, 'the sea at night', []);
		const body = await capturedSubmission(page);
		const cited = body.form_data.reference_mods[0].model as string;

		const removed = await start(page, { ...body, form_data: { ...body.form_data, reference_mods: [] } });
		expect(removed.status()).toBeGreaterThanOrEqual(400);
		expect(await removed.text()).toContain(`removed from this field (${cited})`);

		const muted = await start(page, {
			...body,
			form_data: { ...body.form_data, reference_mods: [{ ...body.form_data.reference_mods[0], strength: 0 }] }
		});
		expect(muted.status()).toBeGreaterThanOrEqual(400);
		expect(await muted.text()).toContain(`removed from this field (${cited})`);
	});
});
