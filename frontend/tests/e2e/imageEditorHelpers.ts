import { test, expect, type Page, type Locator } from '@playwright/test';
import { deflateSync } from 'node:zlib';
import { ownerToken } from './helpers';

const PRESET_ID = '01M31Y6WNM07EHAXXCDV1QVJVX';
export const SIZES = [
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

export function fixturePng(size: number): Buffer {
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

export async function prepare(page: Page): Promise<string> {
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

export async function openControlTab(page: Page, mobile: boolean, name: string): Promise<Locator> {
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
		await page.keyboard.press('Escape');
		await expect(sheet).toBeHidden();
		const settings = page.getByRole('dialog', { name: 'Settings' });
		const settingsButton = page.getByRole('button', { name: 'Settings' }).first();
		await expect(settings.or(settingsButton)).toBeVisible();
		if (!(await settings.isVisible())) await settingsButton.click();
		await expect(settings).toBeVisible();
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
	await expect(tab).toHaveAttribute('aria-selected', 'true');
	return root;
}

export async function docBox(canvas: Locator): Promise<{ x: number; y: number; width: number; height: number }> {
	const raw = await canvas.getAttribute('data-doc-box');
	if (!raw) throw new Error('canvas has no document box');
	const [x, y, width, height] = raw.split(',').map(Number);
	return { x, y, width, height };
}

export async function strokeAcross(page: Page, canvas: Locator, from: [number, number], to: [number, number]) {
	const box = await canvas.boundingBox();
	if (!box) throw new Error('canvas has no box');
	const doc = await docBox(canvas);
	const at = (f: [number, number]) => ({
		x: box.x + doc.x + doc.width * f[0],
		y: box.y + doc.y + doc.height * f[1]
	});
	const a = at(from);
	const b = at(to);
	await page.mouse.move(a.x, a.y);
	await page.mouse.down();
	await page.mouse.move((a.x + b.x) / 2, (a.y + b.y) / 2, { steps: 6 });
	await page.mouse.move(b.x, b.y, { steps: 6 });
	await page.mouse.up();
}

export async function uploadSample(page: Page, field: Locator, size: number): Promise<void> {
	await field.scrollIntoViewIfNeeded();
	const [response] = await Promise.all([
		page.waitForResponse((r) => r.url().includes('/api/media/upload') && r.request().method() === 'POST'),
		field.locator('input[type="file"]').first().setInputFiles({
			name: 'sample.png',
			mimeType: 'image/png',
			buffer: fixturePng(size)
		})
	]);
	expect(response.ok()).toBeTruthy();
	await expect(field.locator('[data-media-inspector]')).toBeVisible({ timeout: 15000 });
}

export async function openEditField(page: Page, mobile: boolean, size: number) {
	const name = await prepare(page);
	const root = await openControlTab(page, mobile, name);
	const field = root.locator('[data-field-name]:has(input[type="file"])').first();
	await uploadSample(page, field, size);
	return field;
}

export async function stagePixel(canvas: Locator, fx: number, fy: number): Promise<number[]> {
	const doc = await docBox(canvas);
	return canvas.evaluate(
		(el, [x, y, w, h, u, v]) => {
			const c = el as HTMLCanvasElement;
			const scale = c.width / c.clientWidth;
			const g = c.getContext('2d')!;
			const data = g.getImageData(Math.round((x + w * u) * scale), Math.round((y + h * v) * scale), 1, 1).data;
			return [data[0], data[1], data[2], data[3]];
		},
		[doc.x, doc.y, doc.width, doc.height, fx, fy]
	);
}

export async function expectEditorReady(canvas: Locator): Promise<void> {
	await expect(canvas).toBeVisible();
	await expect(canvas).toHaveAttribute('data-doc-box', /^-?[\d.]+,-?[\d.]+,[1-9][\d.]*,[1-9][\d.]*$/);
}

export function visibleText(scope: Locator, text: string): Locator {
	return scope.locator(':visible', { hasText: new RegExp(`^${text}$`) }).first();
}

export async function uploadedStats(field: Locator) {
	return field.locator('img').first().evaluate(async (img: HTMLImageElement) => {
		await img.decode();
		const c = document.createElement('canvas');
		c.width = img.naturalWidth;
		c.height = img.naturalHeight;
		const g = c.getContext('2d')!;
		g.drawImage(img, 0, 0);
		const data = g.getImageData(0, 0, c.width, c.height).data;
		let white = 0;
		let dark = 0;
		let transparent = 0;
		for (let i = 0; i < data.length; i += 4) {
			if (data[i + 3] < 10) transparent++;
			else if (data[i] > 250 && data[i + 1] > 250 && data[i + 2] > 250) white++;
			else if (data[i] < 40 && data[i + 1] < 40 && data[i + 2] < 40) dark++;
		}
		return { width: c.width, height: c.height, white, dark, transparent };
	});
}
