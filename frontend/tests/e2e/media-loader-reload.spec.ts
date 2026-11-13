import { test, expect, type Page, type Locator } from '@playwright/test';
import { deflateSync } from 'node:zlib';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { chunk } from './mediaFieldHelpers';
import { presetOption } from './presetPreamble';

const JOURNEY = 'media-loader-reload';
const PRESET_ID = '01M31Y6WNM07EHAXXCDV1QVJVX';

function fixturePng(size: number): Buffer {
	const rows: Buffer[] = [];
	for (let y = 0; y < size; y++) {
		const row = Buffer.alloc(1 + size * 3);
		for (let x = 0; x < size; x++) {
			row[1 + x * 3] = 40 + (x * 120) / size;
			row[2 + x * 3] = 40 + (y * 120) / size;
			row[3 + x * 3] = 90;
		}
		rows.push(row);
	}
	const ihdr = Buffer.alloc(13);
	ihdr.writeUInt32BE(size, 0);
	ihdr.writeUInt32BE(size, 4);
	ihdr[8] = 8;
	ihdr[9] = 2;
	return Buffer.concat([
		Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
		chunk('IHDR', ihdr),
		chunk('IDAT', deflateSync(Buffer.concat(rows))),
		chunk('IEND', Buffer.alloc(0))
	]);
}

async function prepare(page: Page): Promise<string> {
	const token = await ownerToken(page);
	const headers = { Authorization: `Bearer ${token}` };
	const list = await page.request.get('/api/presets?include_uninstalled=true', { headers });
	const presets = ((await list.json()).data || []) as Array<{ id: string; name: string; installed?: boolean }>;
	const preset = presets.find((p) => p.id === PRESET_ID);
	test.skip(!preset, 'Qwen-Image-2.1 preset is not available on this instance');
	if (!preset!.installed) {
		const res = await page.request.post(`/api/presets/${PRESET_ID}/install`, { headers });
		expect(res.ok(), `install -> ${res.status()}`).toBeTruthy();
	}
	const me = await page.request.get('/api/auth/me', { headers });
	const userId = (await me.json()).data.id as string;
	const assign = await page.request.post(`/api/presets/${PRESET_ID}/assign`, {
		headers,
		data: { user_ids: [userId] }
	});
	expect(assign.ok(), `assign -> ${assign.status()}`).toBeTruthy();
	return preset!.name;
}

async function openControlTab(page: Page, mobile: boolean, name: string): Promise<Locator> {
	await page.goto('/generate');
	if (mobile) {
		await expect(page.locator('.studio-dock')).toBeVisible({ timeout: 15000 });
		await page.getByRole('button', { name: 'Open preset and session' }).click();
		const sheet = page.getByRole('dialog', { name: 'Preset and session' });
		await expect(sheet).toBeVisible();
		await sheet.locator('button[aria-haspopup="dialog"]').first().click();
		await presetOption(page, name).click();
		await expect(page.getByRole('dialog', { name: 'Choose preset' })).toBeHidden();
		await sheet.getByTestId('preset-header-mode').getByRole('button').first().click();
		await page.getByRole('option', { name: /^Structure/ }).first().click();
		await page.keyboard.press('Escape');
		if (!(await page.getByRole('dialog', { name: 'Settings' }).isVisible())) {
			await page.getByRole('button', { name: 'Settings' }).first().click();
		}
	} else {
		await page.getByRole('button', { name: 'Choose a preset' }).click();
		await presetOption(page, name).click();
		await expect(page.getByRole('dialog', { name: 'Choose preset' })).toBeHidden();
		await page.getByTestId('preset-header-mode').getByRole('button').first().click();
		await page.getByRole('option', { name: /^Structure/ }).first().click();
	}
	const root = mobile ? page.getByRole('dialog', { name: 'Settings' }) : page.locator('body');
	const tab = page.getByRole('tab', { name: 'Control', exact: true }).first();
	await expect(tab).toBeVisible({ timeout: 20000 });
	await tab.click();
	return root;
}

async function expectPreviewLoaded(page: Page) {
	const tab = page.getByRole('tab', { name: 'Control', exact: true }).first();
	await expect(tab).toBeVisible({ timeout: 20000 });
	await tab.click();
	const field = page.locator('[data-field-name]:has(input[type="file"])').first();
	const img = field.locator('img').first();
	await expect(img).toBeVisible();
	await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(0);
}

test('an uploaded image keeps its preview after a page reload', async ({ page }) => {
	test.setTimeout(240000);
	await page.setViewportSize({ width: 1440, height: 900 });
	await loginAsOwner(page);
	const name = await prepare(page);
	const root = await openControlTab(page, false, name);

	const field = root.locator('[data-field-name]:has(input[type="file"])').first();
	await field.scrollIntoViewIfNeeded();
	await Promise.all([
		page.waitForResponse((r) => r.url().includes('/api/media/upload') && r.request().method() === 'POST'),
		field.locator('input[type="file"]').first().setInputFiles({
			name: 'sample.png',
			mimeType: 'image/png',
			buffer: fixturePng(256)
		})
	]);
	await expectPreviewLoaded(page);
	await page.waitForTimeout(1000);

	await page.reload();
	await expectPreviewLoaded(page);
	await screenshot(page, JOURNEY, 'upload-after-reload');
});

test('an image picked from the upload library keeps its preview after a page reload', async ({ page }) => {
	test.setTimeout(240000);
	await page.setViewportSize({ width: 1440, height: 900 });
	await loginAsOwner(page);
	const name = await prepare(page);
	const token = await ownerToken(page);
	const up = await page.request.post('/api/media/upload', {
		headers: { Authorization: `Bearer ${token}` },
		multipart: { file: { name: 'library-sample.png', mimeType: 'image/png', buffer: fixturePng(256) } }
	});
	expect(up.ok(), `upload -> ${up.status()}`).toBeTruthy();
	const root = await openControlTab(page, false, name);

	const field = root.locator('[data-field-name]:has(input[type="file"])').first();
	await field.scrollIntoViewIfNeeded();
	await field.getByRole('button', { name: 'Library', exact: true }).click();
	await page.locator('[data-pick-tile] [data-pick-main]').first().click();
	await expectPreviewLoaded(page);
	await page.waitForTimeout(1000);

	await page.reload();
	await expectPreviewLoaded(page);
	await screenshot(page, JOURNEY, 'library-after-reload');
});
