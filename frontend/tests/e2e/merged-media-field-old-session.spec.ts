import { test, expect, type Page } from '@playwright/test';
import { deflateSync } from 'node:zlib';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'merged-media-field-old-session';
const PRESET_ID = '01KX47H3MINIMAX000000000VA';
const PRESET_NAME = 'MiniMax-H3';
const MODE = 'refs';
const TAB_ID = 'merged-field-old-tab';
const TABS_STORAGE_KEY = 'potionui_tabs_state';
const SEED_FLAG = 'merged-field-old-seeded';
const SIZES = [
	{ tag: '1440', width: 1440, height: 900, mobile: false },
	{ tag: '390', width: 390, height: 844, mobile: true }
];

type Kind = 'image' | 'video' | 'audio';
type Item = Record<string, unknown>;

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

async function upload(page: Page, token: string, kind: Kind, name: string, mimeType: string, buffer: Buffer): Promise<Item> {
	const res = await page.request.post('/api/media/upload', {
		headers: { Authorization: `Bearer ${token}` },
		multipart: { file: { name, mimeType, buffer } }
	});
	expect(res.ok(), `upload ${name} -> ${res.status()}`).toBeTruthy();
	const body = await res.json();
	expect(body.success, `upload ${name} -> ${JSON.stringify(body)}`).toBeTruthy();
	const data = body.data;
	return {
		path: data.path,
		relative_path: data.relative_path,
		url: data.url,
		name: data.filename ?? name,
		type: kind,
		metadata: {
			...(data.width ? { width: data.width } : {}),
			...(data.height ? { height: data.height } : {}),
			...(data.duration_seconds ? { duration_seconds: data.duration_seconds } : {}),
			size: data.size
		}
	};
}

function keyOf(item: Item): string {
	return (item.relative_path || item.path || item.url) as string;
}

async function prepareHost(page: Page, token: string): Promise<string> {
	const headers = { Authorization: `Bearer ${token}` };
	const list = await page.request.get('/api/presets?include_uninstalled=true', { headers });
	const presets = ((await list.json()).data || []) as Array<{ id: string; name: string; installed?: boolean }>;
	const preset = presets.find((p) => p.id === PRESET_ID) || presets.find((p) => p.name === PRESET_NAME);
	test.skip(!preset, 'MiniMax-H3 preset is not available on this instance');
	if (!preset!.installed) {
		const res = await page.request.post(`/api/presets/${preset!.id}/install`, { headers });
		expect(res.ok(), `install -> ${res.status()}`).toBeTruthy();
	}
	const me = await page.request.get('/api/auth/me', { headers });
	const userId = (await me.json()).data.id as string;
	const assign = await page.request.post(`/api/presets/${preset!.id}/assign`, {
		headers,
		data: { user_ids: [userId] }
	});
	expect(assign.ok(), `assign -> ${assign.status()}`).toBeTruthy();
	return preset!.id;
}

interface OldSession {
	presetId: string;
	image: Item;
	video: Item;
	audio: Item;
}

function oldPrompt(old: OldSession): { content: string; resources: Record<string, { field: string; item_key: string }> } {
	const content =
		`subject_definitions: <Subject 1> is shown in @[references:${keyOf(old.image)}], ` +
		`moving like @[reference_videos:${keyOf(old.video)}] and speaking over @[reference_audios:${keyOf(old.audio)}].`;
	return {
		content,
		resources: {
			'res-image': { field: 'references', item_key: keyOf(old.image) },
			'res-video': { field: 'reference_videos', item_key: keyOf(old.video) },
			'res-audio': { field: 'reference_audios', item_key: keyOf(old.audio) }
		}
	};
}

function oldDirector(old: OldSession) {
	const { content, resources } = oldPrompt(old);
	return {
		schema_version: 1,
		mode: 'director',
		chain: {
			fps: 24,
			segments: [
				{
					id: 'old-shot-1',
					prompt: content,
					prompt_segments: [
						{ id: 'old-shot-1-prompt-0', type: 'content', content, chips: {}, resources, enabled: true }
					],
					duration: 5
				}
			]
		}
	};
}

function oldModeState(old: OldSession) {
	const { content, resources } = oldPrompt(old);
	return {
		selectedPreset: old.presetId,
		selectedMode: MODE,
		prompt: '',
		negativePrompt: '',
		promptSegments: [{ type: 'content', content, chips: {}, resources, enabled: true }],
		negativePromptSegments: [],
		videoDirector: oldDirector(old),
		formData: {
			references: [old.image],
			reference_videos: [old.video],
			reference_audios: [old.audio]
		}
	};
}

async function seedSession(page: Page, token: string, old: OldSession): Promise<string> {
	const res = await page.request.post('/api/sessions/save', {
		headers: { Authorization: `Bearer ${token}` },
		data: {
			preset_id: old.presetId,
			name: `E2E Old Refs ${Date.now()}`,
			mode: MODE,
			data: { [MODE]: oldModeState(old) }
		}
	});
	const body = await res.json();
	expect(res.ok() && body.success, `session save -> ${res.status()} ${JSON.stringify(body)}`).toBeTruthy();
	const id = body.data?.id ?? body.data?.session?.id;
	expect(id, 'saved session id').toBeTruthy();
	return id as string;
}

function oldTab(old: OldSession, sessionId: string | null) {
	const state = oldModeState(old);
	return {
		id: TAB_ID,
		name: 'Old refs',
		selectedPreset: old.presetId,
		selectedMode: MODE,
		selectedVariant: null,
		selectedSessionId: sessionId,
		activeGenerationId: null,
		prompt: state.prompt,
		negativePrompt: state.negativePrompt,
		promptSegments: state.promptSegments.map((segment, index) => ({ ...segment, id: `old-seg-${index}` })),
		negativePromptSegments: [],
		videoDirector: state.videoDirector,
		formData: state.formData
	};
}

async function openAsOldTab(page: Page, tab: unknown) {
	await page.addInitScript(
		({ key, flag, tab }) => {
			try {
				if (sessionStorage.getItem(flag)) return;
				sessionStorage.setItem(flag, '1');
				localStorage.setItem(key, JSON.stringify({ tabs: [tab], activeTabId: (tab as { id: string }).id }));
			} catch {}
		},
		{ key: TABS_STORAGE_KEY, flag: SEED_FLAG, tab }
	);
	await page.goto('/generate');
}

async function openReferencesField(page: Page, mobile: boolean) {
	if (mobile) {
		await expect(page.locator('.studio-dock')).toBeVisible({ timeout: 20000 });
		const sheet = page.getByRole('dialog', { name: 'Settings' });
		const sheetTab = sheet.getByRole('tab', { name: 'References', exact: true }).first();
		await expect(async () => {
			if (!(await sheet.isVisible())) {
				await page.getByRole('button', { name: 'Settings', exact: true }).click();
				await expect(sheet).toBeVisible({ timeout: 5000 });
			}
			try {
				await expect(sheetTab).toBeVisible({ timeout: 10000 });
			} catch (error) {
				await sheet.getByRole('button', { name: 'Done', exact: true }).click({ timeout: 2000 }).catch(() => {});
				throw error;
			}
		}).toPass({ timeout: 60000 });
		await sheetTab.click();
		return page.locator('[data-field-name="references"]').first();
	}
	const tab = page.getByRole('tab', { name: 'References', exact: true }).first();
	await expect(tab).toBeVisible({ timeout: 30000 });
	await tab.click();
	return page.locator('[data-field-name="references"]').first();
}

async function expectMergedField(page: Page, mobile: boolean) {
	const field = await openReferencesField(page, mobile);
	await expect(field).toBeVisible({ timeout: 20000 });
	const strip = field.locator('[data-media-strip]');
	await expect(strip).toBeVisible({ timeout: 20000 });
	await expect(strip.locator('[data-media-group="image"] [data-media-tile]')).toHaveCount(1);
	await expect(strip.locator('[data-media-group="video"] [data-media-tile]')).toHaveCount(1);
	await expect(strip.locator('[data-media-group="audio"] [data-media-tile]')).toHaveCount(1);
	const order = await strip.locator('[data-media-group]').evaluateAll((els) => els.map((el) => el.getAttribute('data-media-group')));
	expect(order).toEqual(['image', 'video', 'audio']);
	await expect(page.locator('[data-field-name="reference_videos"]')).toHaveCount(0);
	await expect(page.locator('[data-field-name="reference_audios"]')).toHaveCount(0);
	return field;
}

async function expectChipsResolved(page: Page, old: OldSession) {
	const director = page.locator('section.video-director[aria-label="Video Director"]');
	await expect(director).toBeVisible({ timeout: 30000 });
	const expected: Array<[Item, string]> = [
		[old.image, 'Picture 1'],
		[old.video, 'Video 1'],
		[old.audio, 'Audio 1']
	];
	for (const [item, label] of expected) {
		const chip = director.locator(`[data-resource-marker="@[references:${keyOf(item)}]"]`).first();
		await expect(chip, `chip for ${label}`).toBeVisible({ timeout: 20000 });
		await expect(chip).toContainText(label);
		await expect(chip.locator('.dangling')).toHaveCount(0);
	}
	await expect(director.locator('[data-resource-marker^="@[reference_videos:"]')).toHaveCount(0);
	await expect(director.locator('[data-resource-marker^="@[reference_audios:"]')).toHaveCount(0);
}

async function expectStoredMarkersRewritten(page: Page, old: OldSession) {
	await expect
		.poll(
			async () =>
				page.evaluate((key) => localStorage.getItem(key) ?? '', TABS_STORAGE_KEY).then((raw) => ({
					rewritten: raw.includes(`@[references:${keyOf(old.video)}]`) && raw.includes(`@[references:${keyOf(old.audio)}]`),
					oldVideo: raw.includes('@[reference_videos:'),
					oldAudio: raw.includes('@[reference_audios:'),
					oldResourceField: raw.includes('"field":"reference_videos"') || raw.includes('"field":"reference_audios"')
				})),
			{ timeout: 20000, message: 'persisted tab state must hold only the rewritten markers' }
		)
		.toEqual({ rewritten: true, oldVideo: false, oldAudio: false, oldResourceField: false });
}

async function fixtures(page: Page, token: string): Promise<{ image: Item; video: Item; audio: Item }> {
	const webm = await recordWebm(page);
	test.skip(!webm, 'This browser cannot record a webm fixture');
	const stamp = Date.now();
	const image = await upload(page, token, 'image', `old-ref-${stamp}.png`, 'image/png', fixturePng(64));
	const video = await upload(page, token, 'video', `old-ref-${stamp}.webm`, 'video/webm', webm!);
	const audio = await upload(page, token, 'audio', `old-ref-${stamp}.wav`, 'audio/wav', fixtureWav(1));
	return { image, video, audio };
}

for (const size of SIZES) {
	test.describe(`old MiniMax-H3 references session at ${size.tag}`, () => {
		test.beforeEach(async ({ page }) => {
			await page.setViewportSize({ width: size.width, height: size.height });
			await loginAsOwner(page);
		});

		test('an old saved session shows its images, videos and audio in the one references field', async ({ page }) => {
			test.setTimeout(240000);
			const token = await ownerToken(page);
			const presetId = await prepareHost(page, token);
			const old: OldSession = { presetId, ...(await fixtures(page, token)) };
			const sessionId = await seedSession(page, token, old);

			await openAsOldTab(page, oldTab(old, sessionId));
			await expectMergedField(page, size.mobile);
			await screenshot(page, JOURNEY, `session-field-${size.tag}`);

			await expectStoredMarkersRewritten(page, old);
			await page.reload();
			await expectMergedField(page, size.mobile);
			await expectStoredMarkersRewritten(page, old);
		});

		test('an old unsaved tab shows its images, videos and audio in the one references field', async ({ page }) => {
			test.setTimeout(240000);
			const token = await ownerToken(page);
			const presetId = await prepareHost(page, token);
			const old: OldSession = { presetId, ...(await fixtures(page, token)) };

			await openAsOldTab(page, oldTab(old, null));
			await expectMergedField(page, size.mobile);
			await screenshot(page, JOURNEY, `tab-field-${size.tag}`);

			await expectStoredMarkersRewritten(page, old);
			await page.reload();
			await expectMergedField(page, size.mobile);
			await expectStoredMarkersRewritten(page, old);
		});

		if (!size.mobile) {
			test('old prompt markers resolve to chips and survive a reload', async ({ page }) => {
				test.setTimeout(240000);
				const token = await ownerToken(page);
				const presetId = await prepareHost(page, token);
				const old: OldSession = { presetId, ...(await fixtures(page, token)) };
				const sessionId = await seedSession(page, token, old);

				await openAsOldTab(page, oldTab(old, sessionId));
				await expectChipsResolved(page, old);
				await screenshot(page, JOURNEY, `session-chips-${size.tag}`);

				await expectStoredMarkersRewritten(page, old);
				await page.reload();
				await expectChipsResolved(page, old);
			});
		}
	});
}
