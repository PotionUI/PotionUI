import { test, expect, type Page, type Locator } from '@playwright/test';
import { deflateSync } from 'node:zlib';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { maskChip, pickFieldTool } from './mediaFieldHelpers';

const JOURNEY = 'qwen21-control-mode-shots';
const PRESET_ID = '01M31Y6WNM07EHAXXCDV1QVJVX';
const SIZES = [
	{ tag: '1440', width: 1440, height: 900 },
	{ tag: '390', width: 390, height: 844 }
];

function crc32(buf: Buffer): number {
	let c = ~0;
	for (const b of buf) {
		c ^= b;
		for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
	}
	return ~c >>> 0;
}

function chunk(type: string, data: Buffer): Buffer {
	const head = Buffer.alloc(4);
	head.writeUInt32BE(data.length);
	const body = Buffer.concat([Buffer.from(type), data]);
	const tail = Buffer.alloc(4);
	tail.writeUInt32BE(crc32(body));
	return Buffer.concat([head, body, tail]);
}

function fixturePng(size: number, seed: number): Buffer {
	const rows: Buffer[] = [];
	for (let y = 0; y < size; y++) {
		const row = Buffer.alloc(1 + size * 3);
		for (let x = 0; x < size; x++) {
			row[1 + x * 3] = (x * 255) / size;
			row[2 + x * 3] = (y * 255) / size;
			row[3 + x * 3] = (seed + ((x ^ y) & 64) * 2) & 255;
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

const served = new Map<string, Buffer>();
let pending: Buffer = Buffer.alloc(0);

async function serveUploads(page: Page) {
	await page.route('**/api/media/tmp/*', async (route) => {
		const file = route.request().url().split('/').pop() || '';
		await route.fulfill({ status: 200, contentType: 'image/png', body: served.get(file) ?? pending });
	});
}

async function upload(page: Page, input: Locator, name: string, buffer: Buffer) {
	pending = buffer;
	const [response] = await Promise.all([
		page.waitForResponse((r) => r.url().includes('/api/media/upload') && r.request().method() === 'POST'),
		input.setInputFiles({ name, mimeType: 'image/png', buffer })
	]);
	const match = (await response.text()).match(/[0-9a-f-]{36}\.png/);
	if (match) served.set(match[0], buffer);
	await page.waitForTimeout(1200);
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
	const assign = await page.request.post(`/api/presets/${PRESET_ID}/assign`, { headers, data: { user_ids: [userId] } });
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
		await page.getByText(name, { exact: true }).first().click();
		await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
		await sheet.getByTestId('preset-header-mode').getByRole('button').first().click();
		await page.getByRole('option', { name: /^Control/ }).first().click();
		await page.waitForTimeout(600);
		await page.keyboard.press('Escape');
		await page.waitForTimeout(400);
		if (!(await page.getByRole('dialog', { name: 'Settings' }).isVisible())) {
			await page.getByRole('button', { name: 'Settings' }).first().click();
		}
	} else {
		await page.getByRole('button', { name: 'Choose a preset' }).click();
		await page.getByText(name, { exact: true }).first().click();
		await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
		await page.getByTestId('preset-header-mode').getByRole('button').first().click();
		await page.getByRole('option', { name: /^Control/ }).first().click();
	}
	const root = mobile ? page.getByRole('dialog', { name: 'Settings' }) : page.locator('body');
	const tab = page.getByRole('tab', { name: 'Control', exact: true }).first();
	await expect(tab).toBeVisible({ timeout: 20000 });
	await tab.click();
	await expect(root.getByText('Guide', { exact: true }).first()).toBeVisible();
	await page.waitForTimeout(400);
	return root;
}

async function center(loc: Locator) {
	await loc.evaluate((el) => el.scrollIntoView({ block: 'center' }));
}

async function top(root: Locator) {
	await root.getByText('Image', { exact: true }).first().evaluate((el) => el.scrollIntoView({ block: 'start' }));
}

async function shoot(page: Page, label: string) {
	await page.mouse.move(page.viewportSize()!.width - 2, 2);
	await page.waitForTimeout(600);
	await screenshot(page, JOURNEY, label);
}

async function choose(page: Page, root: Locator, field: string, option: string, nth = 0) {
	const trigger = root.getByText(field, { exact: true }).nth(nth).locator('xpath=following::button[1]');
	await center(trigger);
	await trigger.click();
	await page.getByText(option, { exact: true }).last().click();
	await page.waitForTimeout(400);
}

for (const size of SIZES) {
	test(`qwen-image-2.1 control mode at ${size.tag}`, async ({ page }) => {
		const mobile = size.width < 600;
		await page.setViewportSize({ width: size.width, height: size.height });
		await serveUploads(page);
		await loginAsOwner(page);
		const name = await prepare(page);
		const root = await openControlTab(page, mobile, name);
		const tag = size.tag;

		await top(root);
		await shoot(page, `01-default-${tag}`);

		await upload(page, root.locator('input[type="file"]').first(), 'source.png', fixturePng(256, 40));
		const mediaField = root.locator('[data-field-name]:has(input[type="file"])').first();
		await expect(mediaField.locator('[data-tools-trigger]')).toBeVisible({ timeout: 20000 });
		await top(root);
		await shoot(page, `02-image-uploaded-${tag}`);

		await pickFieldTool(page, mediaField, 'mask');
		const canvas = page.locator('canvas.cursor-none').first();
		await expect(canvas).toBeVisible({ timeout: 15000 });
		await page.waitForTimeout(600);
		const box = (await canvas.boundingBox())!;
		const cx = box.x + box.width / 2;
		const cy = box.y + box.height / 2;
		await page.mouse.move(cx - box.width * 0.25, cy - box.height * 0.2);
		await page.mouse.down();
		for (let i = 0; i <= 20; i++) {
			await page.mouse.move(cx - box.width * 0.25 + i * box.width * 0.025, cy - box.height * 0.2 + Math.sin(i / 3) * box.height * 0.1, { steps: 3 });
		}
		await page.mouse.move(cx + box.width * 0.2, cy + box.height * 0.2, { steps: 8 });
		await page.mouse.move(cx - box.width * 0.2, cy + box.height * 0.2, { steps: 8 });
		await page.mouse.up();
		await shoot(page, `03-mask-editor-painting-${tag}`);

		const save = page.getByRole('button', { name: /^(Save as new|Use mask|Apply|Save mask|Save)/ }).last();
		const [stored] = await Promise.all([
			page.waitForResponse((r) => r.request().method() === 'POST' && r.status() < 400, { timeout: 20000 }).catch(() => null),
			save.click()
		]);
		void stored;
		await expect(maskChip(mediaField)).toBeVisible({ timeout: 20000 });
		await top(root);
		await shoot(page, `04-mask-applied-${tag}`);

		const extractLabel = root.getByText('Extract the guide from a photo', { exact: true }).first();
		const extractBox = root.locator('[data-field-name="guide_extract"] input[type="checkbox"]');
		await center(extractLabel);
		await expect(root.getByText('Turn off when your image already is a pose, edge or depth map.', { exact: true })).toBeVisible();
		await expect(extractBox).toBeChecked();
		await shoot(page, `05a-extract-on-${tag}`);
		await extractLabel.click();
		await expect(extractBox).not.toBeChecked();
		await shoot(page, `05b-extract-off-${tag}`);
		await extractLabel.click();
		await expect(extractBox).toBeChecked();

		const checkboxLabel = root.getByText('Take the guide from a different image', { exact: true }).first();
		const guideImage = root.getByText('Guide image', { exact: true });
		await center(checkboxLabel);
		await expect(root.getByText('For example, a photo of the pose you want.', { exact: true })).toBeVisible();
		await expect(guideImage).toHaveCount(0);
		await shoot(page, `06a-guide-checkbox-off-${tag}`);

		await checkboxLabel.click();
		await expect(guideImage.first()).toBeVisible();
		await center(guideImage.first());
		await shoot(page, `06b-guide-checkbox-ticked-${tag}`);

		await checkboxLabel.click();
		await expect(guideImage).toHaveCount(0);
		await checkboxLabel.click();
		await expect(guideImage.first()).toBeVisible();

		await choose(page, root, 'Guide', 'Grayscale', 1);
		await expect(root.getByText('Extract the guide from a photo', { exact: true })).toHaveCount(0);
		await center(root.getByText('Guide', { exact: true }).nth(1));
		await shoot(page, `07-guide-grayscale-${tag}`);

		await choose(page, root, 'Guide', 'None', 1);
		await expect(root.getByText('Extract the guide from a photo', { exact: true })).toHaveCount(0);
		await expect(root.getByText('Take the guide from a different image', { exact: true })).toHaveCount(0);
		await expect(root.getByText('Strength', { exact: true })).toHaveCount(0);
		await expect(guideImage).toHaveCount(0);
		await center(root.getByText('Guide', { exact: true }).nth(1));
		await shoot(page, `08-guide-none-${tag}`);
	});
}

for (const size of SIZES) {
	test(`qwen-image-2.1 control advanced view at ${size.tag}`, async ({ page }) => {
		const mobile = size.width < 600;
		await page.setViewportSize({ width: size.width, height: size.height });
		await page.addInitScript(() => localStorage.setItem('potionui-form-audience', 'advanced'));
		await loginAsOwner(page);
		const name = await prepare(page);
		const root = await openControlTab(page, mobile, name);

		const end = root.getByText('Control end', { exact: true }).first();
		await expect(root.getByText('Control start', { exact: true }).first()).toBeVisible();
		await expect(end).toBeVisible();
		await center(end);
		await shoot(page, `09-advanced-guide-sliders-${size.tag}`);
	});
}
