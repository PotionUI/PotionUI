import { test, expect, type Locator, type Page } from '@playwright/test';
import { deflateSync } from 'node:zlib';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'media-field';
const SIZES = [
	{ tag: '1440', width: 1440, height: 900, column: 380 },
	{ tag: '390', width: 390, height: 844, column: 358 }
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

function fixturePng(width: number, height: number, hue: number): Buffer {
	const rows: Buffer[] = [];
	for (let y = 0; y < height; y++) {
		const row = Buffer.alloc(1 + width * 3);
		for (let x = 0; x < width; x++) {
			row[1 + x * 3] = (40 + hue + (x * 120) / width) % 256;
			row[2 + x * 3] = 40 + (y * 120) / height;
			row[3 + x * 3] = 90 + hue / 2;
		}
		rows.push(row);
	}
	const ihdr = Buffer.alloc(13);
	ihdr.writeUInt32BE(width, 0);
	ihdr.writeUInt32BE(height, 4);
	ihdr[8] = 8;
	ihdr[9] = 2;
	return Buffer.concat([
		Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
		chunk('IHDR', ihdr),
		chunk('IDAT', deflateSync(Buffer.concat(rows))),
		chunk('IEND', Buffer.alloc(0))
	]);
}

function fixtureWav(seconds: number): Buffer {
	const rate = 8000;
	const samples = rate * seconds;
	const data = Buffer.alloc(samples * 2);
	for (let i = 0; i < samples; i++) data.writeInt16LE(Math.round(Math.sin(i / 12) * 6000), i * 2);
	const header = Buffer.alloc(44);
	header.write('RIFF', 0);
	header.writeUInt32LE(36 + data.length, 4);
	header.write('WAVEfmt ', 8);
	header.writeUInt32LE(16, 16);
	header.writeUInt16LE(1, 20);
	header.writeUInt16LE(1, 22);
	header.writeUInt32LE(rate, 24);
	header.writeUInt32LE(rate * 2, 28);
	header.writeUInt16LE(2, 32);
	header.writeUInt16LE(16, 34);
	header.write('data', 36);
	header.writeUInt32LE(data.length, 40);
	return Buffer.concat([header, data]);
}

type Item = Record<string, unknown>;

function imageItem(n: number, extra: Item = {}): Item {
	return {
		path: `uploads/e2e-image-${n}.png`,
		relative_path: `uploads/e2e-image-${n}.png`,
		url: `/api/e2e-media/image-${n}.png`,
		name: `harbour-${n}.png`,
		type: 'image',
		metadata: { width: 768, height: 512, size: 145408 },
		...extra
	};
}

function videoItem(n: number): Item {
	return {
		path: `uploads/e2e-video-${n}.webm`,
		relative_path: `uploads/e2e-video-${n}.webm`,
		url: `/api/e2e-media/video-${n}.webm`,
		name: `clip-${n}.webm`,
		type: 'video',
		metadata: { width: 1280, height: 720, duration_seconds: 12, fps: 24, size: 8808038 }
	};
}

function audioItem(n: number): Item {
	return {
		path: `uploads/e2e-audio-${n}.wav`,
		relative_path: `uploads/e2e-audio-${n}.wav`,
		url: `/api/e2e-media/audio-${n}.wav`,
		name: `voice-${n}.wav`,
		type: 'audio',
		metadata: { duration_seconds: 5, size: 225280 }
	};
}

const MIXED = {
	title: 'References',
	multiple: true,
	accepted_types: ['image', 'video', 'audio'],
	max_items_by_kind: { image: 9, video: 3, audio: 3 }
};

async function recordWebm(page: Page): Promise<Buffer | null> {
	const encoded = await page.evaluate(async () => {
		if (typeof MediaRecorder === 'undefined') return null;
		const canvas = document.createElement('canvas');
		canvas.width = 160;
		canvas.height = 90;
		const context = canvas.getContext('2d');
		if (!context) return null;
		const recorder = new MediaRecorder(canvas.captureStream(10), { mimeType: 'video/webm;codecs=vp8' });
		const chunks: Blob[] = [];
		recorder.ondataavailable = (event) => chunks.push(event.data);
		const stopped = new Promise<void>((resolve) => (recorder.onstop = () => resolve()));
		recorder.start();
		for (let i = 0; i < 8; i++) {
			context.fillStyle = `hsl(${i * 40} 50% 40%)`;
			context.fillRect(0, 0, 160, 90);
			await new Promise((resolve) => setTimeout(resolve, 100));
		}
		recorder.stop();
		await stopped;
		const bytes = new Uint8Array(await new Blob(chunks).arrayBuffer());
		let binary = '';
		for (const byte of bytes) binary += String.fromCharCode(byte);
		return btoa(binary);
	});
	return encoded ? Buffer.from(encoded, 'base64') : null;
}


const AUDIO_GENERATION = {
	id: 'gen-audio-1',
	preset_name: 'Voice',
	status: 'completed',
	progress: 1,
	created_at: '2026-01-01T00:00:00Z',
	updated_at: '2026-01-01T00:00:00Z',
	form_data: {},
	files: [
		{
			id: 9001,
			file_path: 'gen-audio-1/take-one.wav',
			file_type: 'audio',
			is_final: true,
			created_at: '2026-01-01T00:00:00Z',
			duration_seconds: 4,
			file_size: 128000
		}
	],
	rating: 0,
	is_favorite: false
};

const IMAGE_GENERATION = {
	...AUDIO_GENERATION,
	id: 'gen-image-1',
	files: [{ ...AUDIO_GENERATION.files[0], id: 9002, file_path: 'gen-image-1/frame.png', file_type: 'image' }]
};

async function mockAudioHistory(page: Page, wav: Buffer) {
	const requested: string[] = [];
	await page.route(/\/api\/generations\/history(\?.*)?$/, async (route) => {
		const url = new URL(route.request().url());
		const kind = url.searchParams.get('media_type');
		requested.push(kind ?? '');
		const all = [AUDIO_GENERATION, IMAGE_GENERATION];
		const generations = kind ? all.filter((g) => g.files.some((f) => f.file_type === kind.toLowerCase())) : all;
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ success: true, data: { generations, total: generations.length } })
		});
	});
	await page.route(/\/api\/tags(\?.*)?$/, (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: { tags: [] } }) })
	);
	await page.route('**/api/media/generations/**', (route) =>
		route.fulfill({ status: 200, contentType: 'audio/wav', body: wav })
	);
	return requested;
}

async function prepare(page: Page, size: (typeof SIZES)[number]) {
	await page.setViewportSize({ width: size.width, height: size.height });
	await loginAsOwner(page);
	await page.goto('/history');
	await page.waitForFunction(() => !!(window as any).__potionui?.components?.MediaLoaderField);

	const webm = await recordWebm(page);
	const wav = fixtureWav(2);
	await page.route('**/e2e-media/**', async (route) => {
		const url = route.request().url();
		if (url.includes('missing')) return route.fulfill({ status: 404, body: 'gone' });
		if (url.endsWith('.wav')) return route.fulfill({ status: 200, contentType: 'audio/wav', body: wav });
		if (url.endsWith('.webm')) {
			if (!webm) return route.fulfill({ status: 404, body: 'no recorder' });
			return route.fulfill({ status: 200, contentType: 'video/webm', body: webm });
		}
		const match = url.match(/image-(\d+)\.png/);
		const hue = match ? Number(match[1]) * 40 : 0;
		return route.fulfill({ status: 200, contentType: 'image/png', body: fixturePng(96, 64, hue) });
	});
	return { hasVideo: webm !== null };
}

async function mountField(page: Page, size: (typeof SIZES)[number], props: Record<string, unknown>) {
	await page.evaluate(
		({ props, column }) => {
			const w = window as any;
			if (w.__mf) {
				w.__mf.entry.unmount(w.__mf.handle);
				w.__mf.el.remove();
			}
			const entry = w.__potionui.components.MediaLoaderField;
			const el = document.createElement('div');
			el.id = 'mf-host';
			el.style.cssText = `position:fixed;top:16px;left:8px;width:${column}px;max-height:calc(100vh - 32px);overflow-y:auto;overflow-x:hidden;z-index:60;padding:12px;`;
			el.className = 'bg-surface-1 border border-line rounded-lg';
			document.body.appendChild(el);
			const state: any = { entry, el, published: [], masks: [] };
			state.handle = entry.mount(el, {
				name: 'refs',
				...props,
				onChange: (_name: string, value: unknown) => {
					state.published.push(value);
					entry.update(state.handle, { value });
				},
				onMaskChange: (_name: string, path: string | undefined) => state.masks.push(path ?? null)
			});
			w.__mf = state;
		},
		{ props, column: size.column }
	);
	return page.locator('#mf-host');
}

async function published(page: Page): Promise<Item[][]> {
	return page.evaluate(() => (window as any).__mf.published);
}

async function expectNoOverflow(page: Page, host: Locator) {
	const metrics = await host.evaluate((el) => {
		const hostRect = el.getBoundingClientRect();
		let widest = 0;
		el.querySelectorAll('*').forEach((child) => {
			const rect = child.getBoundingClientRect();
			if (rect.width === 0) return;
			widest = Math.max(widest, rect.right - hostRect.right);
		});
		return {
			hostScroll: el.scrollWidth - el.clientWidth,
			pageScroll: document.documentElement.scrollWidth - window.innerWidth,
			widest
		};
	});
	expect(metrics.hostScroll, 'field content wider than its column').toBeLessThanOrEqual(0);
	expect(metrics.pageScroll, 'page scrolls horizontally').toBeLessThanOrEqual(0);
	expect(metrics.widest, 'an element sticks out of the column').toBeLessThanOrEqual(1);
}

async function expectToolsReachable(page: Page, host: Locator, expected: string[], shot?: string) {
	const trigger = host.locator('[data-tools-trigger]').first();
	await trigger.scrollIntoViewIfNeeded();
	await trigger.click();
	const menu = page.locator('[data-tools-menu]');
	await expect(menu).toBeVisible();
	const box = await menu.boundingBox();
	const viewport = page.viewportSize()!;
	expect(box).not.toBeNull();
	expect(box!.x).toBeGreaterThanOrEqual(0);
	expect(box!.y).toBeGreaterThanOrEqual(0);
	expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width);
	expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height);
	for (const id of expected) await expect(menu.locator(`[data-tool="${id}"]`)).toBeVisible();
	if (shot) await screenshot(page, JOURNEY, shot);
	await page.keyboard.press('Escape');
	await expect(menu).toHaveCount(0);
}

for (const size of SIZES) {
	test.describe(`media field at ${size.tag}`, () => {
		let hasVideo = true;

		test.beforeEach(async ({ page }) => {
			const prepared = await prepare(page, size);
			hasVideo = prepared.hasVideo;
		});

		test('an empty single field offers a drop zone with every door and no tools', async ({ page }) => {
			const host = await mountField(page, size, {
				config: { title: 'Image', accept: 'image/*', allow_inpaint: true },
				value: null
			});
			const zone = host.locator('[data-media-dropzone]');
			await expect(zone).toBeVisible();
			await expect(zone.getByRole('button', { name: 'Browse' })).toBeVisible();
			await expect(zone.getByRole('button', { name: 'Paste' })).toBeVisible();
			await expect(zone.getByRole('button', { name: 'History' })).toBeVisible();
			await expect(zone.getByRole('button', { name: 'Library' })).toBeVisible();
			await expect(zone.getByRole('button', { name: 'Draw' })).toBeVisible();
			await expect(host.locator('[data-tools-trigger]')).toHaveCount(0);
			await expectNoOverflow(page, host);
			await screenshot(page, JOURNEY, `empty-${size.tag}`);
		});

		test('a single image shows one inspector with chips, tools, replace and remove', async ({ page }) => {
			const host = await mountField(page, size, {
				config: { title: 'Image', accept: 'image/*', allow_inpaint: true },
				value: imageItem(1)
			});
			const inspector = host.locator('[data-media-inspector]');
			await expect(inspector).toBeVisible();
			await expect(inspector.locator('img')).toBeVisible();
			await expect(inspector.getByText('768×512')).toBeVisible();
			await expect(inspector.getByText('PNG', { exact: true })).toBeVisible();
			await expect(inspector.getByRole('button', { name: 'Replace' })).toBeVisible();
			await expect(inspector.getByRole('button', { name: 'Remove' })).toBeVisible();
			await expect(host.locator('[data-media-strip]')).toHaveCount(0);
			await expectNoOverflow(page, host);
			await expectToolsReachable(page, host, ['edit', 'crop', 'mask', 'clear-mask', 'full'], `tools-single-${size.tag}`);
			await screenshot(page, JOURNEY, `single-image-${size.tag}`);
		});

		test('tab order walks the preview, Tools, Replace and Remove', async ({ page }) => {
			const host = await mountField(page, size, {
				config: { title: 'Image', accept: 'image/*' },
				value: imageItem(1)
			});
			await host.getByRole('button', { name: 'View full size' }).focus();
			await page.keyboard.press('Tab');
			await expect(host.locator('[data-tools-trigger]')).toBeFocused();
			await page.keyboard.press('Tab');
			await expect(host.getByRole('button', { name: 'Replace' })).toBeFocused();
			await page.keyboard.press('Tab');
			await expect(host.getByRole('button', { name: 'Remove' })).toBeFocused();
		});

		test('Remove clears a single field in one click', async ({ page }) => {
			const host = await mountField(page, size, {
				config: { title: 'Image', accept: 'image/*' },
				value: imageItem(1)
			});
			await host.getByRole('button', { name: 'Remove' }).click();
			expect((await published(page)).at(-1)).toBeNull();
			await expect(host.locator('[data-media-dropzone]')).toBeVisible();
		});

		test('a painted mask shows a blue MASK chip that clears it', async ({ page }) => {
			await page.route('**/api/media/upload', async (route) => {
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify({
						success: true,
						data: { path: 'uploads/mask-e2e.png', relative_path: 'uploads/mask-e2e.png', url: '/api/e2e-media/image-9.png' }
					})
				});
			});
			const host = await mountField(page, size, {
				config: { title: 'Image', accept: 'image/*', allow_inpaint: true },
				value: imageItem(1)
			});
			await host.locator('[data-tools-trigger]').first().click();
			await page.locator('[data-tools-menu] [data-tool="mask"]').click();
			const dialog = page.getByRole('dialog', { name: 'Create inpainting mask' });
			await expect(dialog).toBeVisible();
			const canvas = dialog.locator('canvas').first();
			const box = await canvas.boundingBox();
			if (!box) throw new Error('mask canvas has no box');
			await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.3);
			await page.mouse.down();
			await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.6, { steps: 6 });
			await page.mouse.up();
			await dialog.getByRole('button', { name: /Save mask/ }).click();
			await expect(dialog).toBeHidden({ timeout: 15000 });

			const chip = host.locator('[data-mask-chip]');
			await expect(chip).toBeVisible();
			await expectNoOverflow(page, host);
			await screenshot(page, JOURNEY, `mask-${size.tag}`);
			await chip.click();
			await expect(chip).toHaveCount(0);
			const masks = await page.evaluate(() => (window as any).__mf.masks);
			expect(masks.at(-1)).toBeNull();
		});

		test('a video and an audio file each get their own inspector and tools', async ({ page }) => {
			test.skip(!hasVideo, 'this browser cannot record a video fixture');
			const video = await mountField(page, size, {
				config: { title: 'Video', accept: 'video/*' },
				value: videoItem(1)
			});
			await expect(video.locator('[data-media-inspector][data-kind="video"] video')).toBeVisible();
			await expect(video.getByText('1280×720')).toBeVisible();
			await expect(video.getByText('24 fps')).toBeVisible();
			await expectNoOverflow(page, video);
			await expectToolsReachable(page, video, ['trim', 'frame', 'full']);
			await screenshot(page, JOURNEY, `video-${size.tag}`);

			const audio = await mountField(page, size, {
				config: { title: 'Audio', accept: 'audio/*' },
				value: audioItem(1)
			});
			await expect(audio.locator('[data-media-inspector][data-kind="audio"] audio')).toBeVisible();
			await expect(audio.getByText('WAV', { exact: true })).toBeVisible();
			await expectNoOverflow(page, audio);
			await expectToolsReachable(page, audio, ['trim', 'split', 'full']);
			await screenshot(page, JOURNEY, `audio-${size.tag}`);
		});

		test('a missing file says so in danger and greys the tools that need it', async ({ page }) => {
			const host = await mountField(page, size, {
				config: { title: 'Image', accept: 'image/*' },
				value: imageItem(1, { url: '/api/e2e-media/missing.png', name: 'gone.png' })
			});
			await expect(host.locator('[data-media-missing]')).toBeVisible();
			await expect(host.getByText('File not found').first()).toBeVisible();
			await expect(host.getByRole('button', { name: 'Replace' })).toBeVisible();
			await host.locator('[data-tools-trigger]').first().click();
			await expect(page.locator('[data-tools-menu] [data-tool="edit"]')).toHaveAttribute('aria-disabled', 'true');
			await page.keyboard.press('Escape');
			await expectNoOverflow(page, host);
			await screenshot(page, JOURNEY, `missing-${size.tag}`);
		});

		test('an upload in flight is shown inside the field footprint', async ({ page }) => {
			await page.route('**/api/media/upload', async (route) => {
				await new Promise((resolve) => setTimeout(resolve, 2500));
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify({
						success: true,
						data: { path: 'uploads/e2e-image-5.png', relative_path: 'uploads/e2e-image-5.png', url: '/api/e2e-media/image-5.png', width: 96, height: 64, size: 3000 }
					})
				});
			});
			const host = await mountField(page, size, {
				config: { title: 'Image', accept: 'image/*' },
				value: null
			});
			await host.locator('input[type="file"]').first().setInputFiles({
				name: 'incoming.png',
				mimeType: 'image/png',
				buffer: fixturePng(96, 64, 80)
			});
			await expect(host.locator('[data-media-uploading]')).toBeVisible();
			await expectNoOverflow(page, host);
			await screenshot(page, JOURNEY, `uploading-${size.tag}`);
			await expect(host.locator('[data-media-inspector] img')).toBeVisible({ timeout: 15000 });
		});

		test('a rejected file is explained above the field and can be dismissed', async ({ page }) => {
			const host = await mountField(page, size, {
				config: { title: 'Image', accept: 'image/*', accepted_types: ['image'] },
				value: null
			});
			await host.locator('input[type="file"]').first().setInputFiles({
				name: 'clip.mp4',
				mimeType: 'video/mp4',
				buffer: Buffer.from('not really a video')
			});
			const rejection = host.locator('[data-media-rejection]');
			await expect(rejection).toBeVisible();
			await expect(rejection).toContainText("not accepted");
			await expectNoOverflow(page, host);
			await rejection.getByRole('button', { name: 'Dismiss' }).click();
			await expect(rejection).toHaveCount(0);
		});

		test('a multi field with three images is one inspector over a numbered strip', async ({ page }) => {
			const host = await mountField(page, size, {
				config: { title: 'Reference images', multiple: true, accepted_types: ['image'], max_items: 10, allow_inpaint: true },
				value: [imageItem(1), imageItem(2), imageItem(3)]
			});
			await expect(host.locator('[data-media-inspector]')).toHaveCount(1);
			const tiles = host.locator('[data-media-tile]');
			await expect(tiles).toHaveCount(3);
			await expect(host.locator('[data-media-eyebrow]')).toHaveCount(0);
			await expect(host.locator('[data-media-count]')).toHaveText('3/10');
			await expect(host.locator('[data-media-add="image"]')).toHaveCount(1);
			await expect(host.locator('[data-media-tile="0"]')).toHaveAttribute('data-selected', 'true');
			await tiles.nth(1).click();
			await expect(host.locator('[data-media-tile="1"]')).toHaveAttribute('data-selected', 'true');
			await expectNoOverflow(page, host);
			await expectToolsReachable(page, host, ['edit', 'crop', 'full', 'earlier', 'later', 'replace', 'remove-all'], `tools-multi-${size.tag}`);
			await screenshot(page, JOURNEY, `multi-three-${size.tag}`);
		});

		test('Alt and the arrow keys reorder inside the strip and plain arrows move the selection', async ({ page }) => {
			const host = await mountField(page, size, {
				config: { title: 'Reference images', multiple: true, accepted_types: ['image'], max_items: 10 },
				value: [imageItem(1), imageItem(2), imageItem(3)]
			});
			const first = host.locator('[data-media-tile="0"]');
			await first.click();
			await first.focus();
			await page.keyboard.press('Alt+ArrowRight');
			const names = ((await published(page)).at(-1) as Item[]).map((item) => item.name);
			expect(names).toEqual(['harbour-2.png', 'harbour-1.png', 'harbour-3.png']);
			await expect(host.locator('[data-media-tile="1"]')).toHaveAttribute('data-selected', 'true');
			await expect(host.locator('[data-media-tile="1"]')).toBeFocused();

			await page.keyboard.press('ArrowRight');
			await expect(host.locator('[data-media-tile="2"]')).toHaveAttribute('data-selected', 'true');
			await page.keyboard.press('Alt+ArrowRight');
			expect(((await published(page)).at(-1) as Item[]).map((item) => item.name)).toEqual([
				'harbour-2.png',
				'harbour-1.png',
				'harbour-3.png'
			]);
		});

		test('Delete on a strip tile removes just that item', async ({ page }) => {
			const host = await mountField(page, size, {
				config: { title: 'Reference images', multiple: true, accepted_types: ['image'], max_items: 10 },
				value: [imageItem(1), imageItem(2), imageItem(3)]
			});
			await host.locator('[data-media-tile="1"]').focus();
			await page.keyboard.press('Delete');
			expect(((await published(page)).at(-1) as Item[]).map((item) => item.name)).toEqual([
				'harbour-1.png',
				'harbour-3.png'
			]);
		});

		test('at the cap the add tile is gone and the count turns amber', async ({ page }) => {
			const host = await mountField(page, size, {
				config: { title: 'Reference images', multiple: true, accepted_types: ['image'], max_items: 10 },
				value: Array.from({ length: 10 }, (_, i) => imageItem(i + 1))
			});
			await expect(host.locator('[data-media-tile]')).toHaveCount(10);
			await expect(host.locator('[data-media-add]')).toHaveCount(0);
			await expect(host.locator('[data-media-count]')).toHaveText('10/10');
			await expect(host.locator('[data-media-count]')).toHaveClass(/text-warning/);
			await expectNoOverflow(page, host);
			const height = await host.evaluate((el) => el.scrollHeight);
			expect(height).toBeLessThan(size.tag === '1440' ? 620 : 700);
			await screenshot(page, JOURNEY, `multi-cap-${size.tag}`);
		});

		test('an empty multi field shows the normal drop zone', async ({ page }) => {
			const host = await mountField(page, size, {
				config: { title: 'Reference images', multiple: true, accepted_types: ['image'], max_items: 10 },
				value: []
			});
			await expect(host.locator('[data-media-dropzone]')).toBeVisible();
			await expect(host.locator('[data-media-count]')).toHaveText('0/10');
			await expectNoOverflow(page, host);
		});

		test('a mixed field groups by kind with its own count, numbering and add tile', async ({ page }) => {
			test.skip(!hasVideo, 'this browser cannot record a video fixture');
			const host = await mountField(page, size, {
				config: MIXED,
				value: [imageItem(1), videoItem(1), imageItem(2), audioItem(1), audioItem(2), imageItem(3)]
			});
			await expect(host.locator('[data-media-inspector]')).toHaveCount(1);
			await expect(host.locator('[data-media-eyebrow="image"]')).toContainText(/images/i);
			await expect(host.locator('[data-media-eyebrow="image"]')).toContainText('3/9');
			await expect(host.locator('[data-media-eyebrow="video"]')).toContainText('1/3');
			await expect(host.locator('[data-media-eyebrow="audio"]')).toContainText('2/3');
			await expect(host.locator('[data-media-group="video"] [data-media-tile]')).toHaveCount(1);
			await expect(host.locator('[data-media-group="audio"] [data-media-tile]')).toHaveCount(2);
			for (const kind of ['image', 'video', 'audio']) {
				await expect(host.locator(`[data-media-add="${kind}"]`)).toHaveCount(1);
			}
			const eyebrows = await host.locator('[data-media-eyebrow]').evaluateAll((nodes) =>
				nodes.map((node) => (node as HTMLElement).dataset.mediaEyebrow)
			);
			expect(eyebrows).toEqual(['image', 'video', 'audio']);
			await expect(host.locator('[data-media-folded]')).toHaveCount(0);

			await host.locator('[data-media-group="video"] [data-media-tile]').first().click();
			await expect(host.locator('[data-media-inspector]')).toHaveAttribute('data-kind', 'video');
			await expect(host.locator('[data-media-handle]').first()).toContainText(/Video 1|V1/);
			await expectNoOverflow(page, host);
			await screenshot(page, JOURNEY, `mixed-groups-${size.tag}`);
		});

		test('empty kind groups keep their eyebrow and an add box that opens a menu for that kind', async ({ page }) => {
			const host = await mountField(page, size, {
				config: MIXED,
				value: [imageItem(1), imageItem(2), imageItem(3)]
			});
			await expect(host.locator('[data-media-folded]')).toHaveCount(0);
			await expect(host.locator('[data-media-eyebrow="video"]')).toContainText('0/3');
			await expect(host.locator('[data-media-eyebrow="audio"]')).toContainText('0/3');
			await expect(host.locator('[data-media-group="video"] [data-media-add="video"]')).toBeVisible();
			await expect(host.locator('[data-media-group="audio"] [data-media-add="audio"]')).toBeVisible();
			await expectNoOverflow(page, host);
			await screenshot(page, JOURNEY, `mixed-empty-groups-${size.tag}`);

			await host.locator('[data-media-add="audio"]').click();
			const menu = page.locator('[data-source-menu]');
			await expect(menu).toBeVisible();
			await expect(menu.locator('[data-source="history"]')).toBeVisible();
			await expect(menu.locator('[data-source="paste"]')).toHaveCount(0);
			await expect(menu.locator('[data-source="browse"]')).toBeVisible();
			await page.keyboard.press('Escape');
			await expect(menu).toHaveCount(0);
		});

		test('a fully empty mixed field shows every group with its add box instead of a drop zone', async ({ page }) => {
			const host = await mountField(page, size, { config: MIXED, value: [] });
			await expect(host.locator('[data-media-dropzone]')).toHaveCount(0);
			await expect(host.locator('[data-media-inspector]')).toHaveCount(0);
			for (const kind of ['image', 'video', 'audio']) {
				await expect(host.locator(`[data-media-group="${kind}"] [data-media-add="${kind}"]`)).toBeVisible();
			}
			await expect(host.locator('[data-media-count]')).toHaveText('0/15');
			await expectNoOverflow(page, host);
			await screenshot(page, JOURNEY, `mixed-empty-${size.tag}`);
		});

		test('a full kind group loses its add tile while the others keep theirs', async ({ page }) => {
			test.skip(!hasVideo, 'this browser cannot record a video fixture');
			const host = await mountField(page, size, {
				config: MIXED,
				value: [videoItem(1), videoItem(2), videoItem(3), imageItem(1)]
			});
			await expect(host.locator('[data-media-eyebrow="video"] [data-media-group-count]')).toHaveText('3/3');
			await expect(host.locator('[data-media-eyebrow="video"] [data-media-group-count]')).toHaveClass(/text-warning/);
			await expect(host.locator('[data-media-group="video"] [data-media-add]')).toHaveCount(0);
			await expect(host.locator('[data-media-group="image"] [data-media-add]')).toHaveCount(1);
		});

		test('reordering inside one kind leaves the other kinds in their slots', async ({ page }) => {
			const host = await mountField(page, size, {
				config: MIXED,
				value: [imageItem(1), audioItem(1), imageItem(2)]
			});
			const firstImage = host.locator('[data-media-group="image"] [data-media-tile]').first();
			await firstImage.click();
			await firstImage.focus();
			await page.keyboard.press('Alt+ArrowRight');
			const names = ((await published(page)).at(-1) as Item[]).map((item) => item.name);
			expect(names).toEqual(['harbour-2.png', 'voice-1.wav', 'harbour-1.png']);
		});

		test('audio can be loaded from history on an audio field and from the empty audio group of a mixed field', async ({ page }) => {
			const requested = await mockAudioHistory(page, fixtureWav(2));

			const single = await mountField(page, size, { config: { title: 'Voice', accept: 'audio/*' }, value: null });
			await single.getByRole('button', { name: 'History' }).click();
			const modalTitle = page.getByText('Select Audio from Generation History');
			await expect(modalTitle).toBeVisible();
			const historyCards = page.getByRole('dialog').locator('[data-testid="history-file-tile"] button');
			await expect(historyCards).toHaveCount(1);
			expect(requested).toContain('audio');
			await screenshot(page, JOURNEY, `audio-history-${size.tag}`);
			await historyCards.first().click();
			await expect(modalTitle).toBeHidden();
			const inspector = single.locator('[data-media-inspector][data-kind="audio"]');
			await expect(inspector).toBeVisible();
			await expect(inspector.locator('audio')).toBeVisible();
			await expect(inspector.getByText('WAV', { exact: true })).toBeVisible();
			await expect(inspector.getByText('4.0s', { exact: true })).toBeVisible();
			expect(((await published(page)).at(-1) as unknown as Item).type).toBe('audio');
			await expectNoOverflow(page, single);

			const mixed = await mountField(page, size, { config: MIXED, value: [imageItem(1)] });
			await mixed.locator('[data-media-add="audio"]').click();
			await page.locator('[data-source-menu] [data-source="history"]').click();
			await expect(page.getByText('Select Audio from Generation History')).toBeVisible();
			const mixedCards = page.getByRole('dialog').locator('[data-testid="history-file-tile"] button');
			await expect(mixedCards).toHaveCount(1);
			await mixedCards.first().click();
			await expect(mixed.locator('[data-media-group="audio"] [data-media-tile]')).toHaveCount(1);
			await expect(mixed.locator('[data-media-eyebrow="audio"]')).toContainText('1/3');
		});

		test('the compact row face lists a single item and its tools in one row', async ({ page }) => {
			const host = await mountField(page, size, {
				config: { title: 'Image', accept: 'image/*', allow_inpaint: true },
				value: imageItem(1),
				compact: true
			});
			await expect(host.locator('[data-media-row-face]')).toBeVisible();
			await expect(host.locator('[data-media-inspector]')).toHaveCount(0);
			const row = host.locator('[data-media-row]').first();
			const box = await row.boundingBox();
			expect(box!.height).toBeLessThan(64);
			await expectNoOverflow(page, host);
			await expectToolsReachable(page, host, ['edit', 'crop', 'full', 'replace', 'remove']);
			await screenshot(page, JOURNEY, `compact-single-${size.tag}`);
		});

		test('the compact row face has a split add button when empty and rows when multi', async ({ page }) => {
			const empty = await mountField(page, size, {
				config: { title: 'Image', accept: 'image/*' },
				value: null,
				compact: true
			});
			await expect(empty.locator('[data-media-row-add]')).toBeVisible();
			await expect(empty.getByRole('button', { name: 'Add image' })).toBeVisible();
			await expect(empty.getByRole('button', { name: 'More ways to add' })).toBeVisible();
			await expectNoOverflow(page, empty);

			const multi = await mountField(page, size, {
				config: { title: 'Reference images', multiple: true, accepted_types: ['image'], max_items: 10 },
				value: [imageItem(1), imageItem(2), imageItem(3)],
				compact: true
			});
			await expect(multi.locator('[data-media-row]')).toHaveCount(3);
			await expectNoOverflow(page, multi);
			await multi.locator('[data-media-grip="0"]').focus();
			await page.keyboard.press('Alt+ArrowDown');
			expect(((await published(page)).at(-1) as Item[]).map((item) => item.name)).toEqual([
				'harbour-2.png',
				'harbour-1.png',
				'harbour-3.png'
			]);
			await screenshot(page, JOURNEY, `compact-multi-${size.tag}`);
		});

		test('a field in a column narrower than 240px falls back to the row face', async ({ page }) => {
			const narrow = { ...size, column: 200 };
			const host = await mountField(page, narrow, {
				config: { title: 'Image', accept: 'image/*' },
				value: imageItem(1)
			});
			await expect(host.locator('[data-media-row-face]')).toBeVisible();
			await expectNoOverflow(page, host);
			await screenshot(page, JOURNEY, `narrow-${size.tag}`);
		});
	});
}
