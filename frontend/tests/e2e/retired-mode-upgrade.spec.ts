import { test, expect, type Page } from '@playwright/test';
import { deflateSync } from 'node:zlib';
import { loginAsOwner, ownerToken, scopedStorageKey, screenshot } from './helpers';
import { chunk } from './mediaFieldHelpers';

const JOURNEY = 'retired-mode-upgrade';
const PRESET_ID = '01KX47H3MINIMAX000000000VA';
const PRESET_NAME = 'MiniMax-H3';
const TABS_STORAGE_KEY = 'potionui_tabs_state';
const SEED_FLAG = 'retired-mode-upgrade-seeded';
const PROMPT = 'A lighthouse on a cliff at dusk, waves breaking below';
const PIECE_PROMPTS = 'The lighthouse lamp turns on\nA storm rolls in from the sea';

type Item = Record<string, unknown>;
type Tab = Record<string, any>;

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

async function upload(page: Page, token: string, kind: 'image' | 'audio', name: string, mimeType: string, buffer: Buffer): Promise<Item> {
	const res = await page.request.post('/api/media/upload', {
		headers: { Authorization: `Bearer ${token}` },
		multipart: { file: { name, mimeType, buffer } }
	});
	const body = await res.json();
	expect(res.ok() && body.success, `upload ${name} -> ${res.status()} ${JSON.stringify(body)}`).toBeTruthy();
	const data = body.data;
	return {
		path: data.path,
		relative_path: data.relative_path,
		url: data.url,
		name: data.filename ?? name,
		type: kind,
		metadata: { ...(data.width ? { width: data.width } : {}), ...(data.height ? { height: data.height } : {}), size: data.size }
	};
}

async function prepareHost(page: Page, token: string): Promise<string> {
	const headers = { Authorization: `Bearer ${token}` };
	const list = await page.request.get('/api/presets?include_uninstalled=true', { headers });
	const presets = ((await list.json()).data || []) as Array<{ id: string; name: string; installed?: boolean }>;
	const preset = presets.find((p) => p.id === PRESET_ID) || presets.find((p) => p.name === PRESET_NAME);
	expect(preset, 'the MiniMax-H3 preset ships with the app').toBeTruthy();
	if (!preset!.installed) {
		const res = await page.request.post(`/api/presets/${preset!.id}/install`, { headers });
		expect(res.ok(), `install -> ${res.status()}`).toBeTruthy();
	}
	const me = await page.request.get('/api/auth/me', { headers });
	const userId = (await me.json()).data.id as string;
	const assign = await page.request.post(`/api/presets/${preset!.id}/assign`, { headers, data: { user_ids: [userId] } });
	expect(assign.ok(), `assign -> ${assign.status()}`).toBeTruthy();
	return preset!.id;
}

interface Media {
	image: Item;
	audio: Item;
}

async function uploadMedia(page: Page, token: string): Promise<Media> {
	const stamp = Date.now();
	return {
		image: await upload(page, token, 'image', `long-start-${stamp}.png`, 'image/png', fixturePng(64)),
		audio: await upload(page, token, 'audio', `long-sound-${stamp}.wav`, 'audio/wav', fixtureWav(1))
	};
}

function oldLongForm(media: Media) {
	return {
		speed_profile: 'quality',
		seed: 777,
		quantity: 1,
		start_image: media.image,
		total_seconds: 40,
		piece_seconds: 14,
		piece_prompts: PIECE_PROMPTS,
		soundtrack: media.audio,
		drift_correction: 0.35,
		resolution: '832x480'
	};
}

function oldVideoForm() {
	return {
		speed_profile: 'quality',
		seed: 424242,
		quantity: 2,
		resolution: '1344x768',
		keyframe_pixel_budget: '2',
		audio_refine: 'lock',
		refine_video_sigma_shift: 9
	};
}

function oldTab(id: string, name: string, presetId: string, mode: string, formData: Record<string, unknown>, sessionId: string | null): Tab {
	return {
		id,
		name,
		selectedPreset: presetId,
		selectedMode: mode,
		selectedVariant: null,
		selectedSessionId: sessionId,
		activeGenerationId: null,
		prompt: PROMPT,
		negativePrompt: '',
		promptSegments: [{ id: `${id}-seg`, type: 'content', content: PROMPT, chips: {}, enabled: true }],
		negativePromptSegments: [],
		formData
	};
}

async function openWithTabs(page: Page, tabs: Tab[], activeId: string) {
	await page.addInitScript(
		({ key, flag, tabs, activeId }) => {
			try {
				if (sessionStorage.getItem(flag)) return;
				sessionStorage.setItem(flag, '1');
				localStorage.setItem(key, JSON.stringify({ tabs, activeTabId: activeId }));
			} catch {}
		},
		{ key: TABS_STORAGE_KEY, flag: SEED_FLAG, tabs, activeId }
	);
	await page.goto('/generate');
}

async function seedSession(page: Page, token: string, presetId: string, mode: string, tab: Tab): Promise<string> {
	const { id: _id, name: _name, selectedSessionId: _sid, ...state } = tab;
	const res = await page.request.post('/api/sessions/save', {
		headers: { Authorization: `Bearer ${token}` },
		data: { preset_id: presetId, name: `E2E Old ${mode} ${Date.now()}`, mode, data: { [mode]: { ...state, selectedMode: mode } } }
	});
	const body = await res.json();
	expect(res.ok() && body.success, `session save -> ${res.status()} ${JSON.stringify(body)}`).toBeTruthy();
	const id = body.data?.id ?? body.data?.session?.id;
	expect(id, 'saved session id').toBeTruthy();
	return id as string;
}

async function storedTabs(page: Page): Promise<Tab[]> {
	const key = await scopedStorageKey(page, TABS_STORAGE_KEY);
	return page.evaluate((k) => {
		try {
			return JSON.parse(localStorage.getItem(k) ?? 'null')?.tabs ?? [];
		} catch {
			return [];
		}
	}, key);
}

async function directorTab(page: Page): Promise<Tab> {
	let found: Tab | undefined;
	await expect
		.poll(
			async () => {
				found = (await storedTabs(page)).find((tab) => tab.selectedMode === 'video' && String(tab.name).endsWith(': Video Director'));
				return !!found;
			},
			{ timeout: 20000, message: 'a Video Director tab should have been created' }
		)
		.toBe(true);
	return found!;
}

function trackPageErrors(page: Page): string[] {
	const errors: string[] = [];
	page.on('pageerror', (error) => errors.push(error.message));
	return errors;
}

const DIRECTOR = 'section.video-director[aria-label="Video Director"]';
const NOTICE = 'This session has a Long video setup.';

function expectConvertedLong(tab: Tab, media: Media) {
	const segments = tab.videoDirector?.chain?.segments ?? [];
	expect(segments, 'a 40 s / 14 s-part setup becomes three shots').toHaveLength(3);
	expect(
		segments.map((segment: Tab) => String(segment.prompt).trim()),
		'each part keeps its own line and the last one falls back to the main prompt'
	).toEqual([...PIECE_PROMPTS.split('\n'), PROMPT]);
	const frames = segments.reduce((sum: number, segment: Tab) => sum + Number(segment.duration), 0);
	expect(frames, 'finished length stays near the 40 s asked for').toBeGreaterThan(39);
	expect(frames).toBeLessThan(42);
	expect(segments[0].keyframe?.relative_path ?? segments[0].keyframe?.path, 'start picture becomes the first shot keyframe').toBe(
		(media.image.relative_path ?? media.image.path) as string
	);
	expect(segments[1].keyframe ?? null).toBeNull();
	const audio = tab.videoDirector?.chain?.audio ?? [];
	expect(audio, 'soundtrack carried over').toHaveLength(1);
	expect(audio[0].media?.relative_path ?? audio[0].media?.path).toBe((media.audio.relative_path ?? media.audio.path) as string);
	expect(tab.formData.resolution).toBe('832x480');
	expect(tab.formData.speed_profile).toBe('quality');
	expect(tab.formData.seed).toBe(777);
	expect(tab.formData.drift_correction).toBe(0.35);
	expect(tab.formData).not.toHaveProperty('total_seconds');
}

test.describe('0.1.0 sessions on MiniMax-H3 after the Long video mode was retired', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await loginAsOwner(page);
	});

	for (const saved of [false, true]) {
		test(`a ${saved ? 'saved session' : 'tab'} stored on Long video opens, offers Video Director and keeps its data until used`, async ({ page }) => {
			test.setTimeout(240000);
			const errors = trackPageErrors(page);
			const token = await ownerToken(page);
			const presetId = await prepareHost(page, token);
			const media = await uploadMedia(page, token);
			const tab = oldTab('old-long-tab', 'Lighthouse long', presetId, 'long', oldLongForm(media), null);
			if (saved) tab.selectedSessionId = await seedSession(page, token, presetId, 'long', tab);

			await openWithTabs(page, [tab], tab.id);

			await expect(page.getByText(NOTICE)).toBeVisible({ timeout: 45000 });
			await expect(page.getByText(/Couldn't load|Something went wrong|not found/i)).toHaveCount(0);
			await screenshot(page, JOURNEY, `long-notice-${saved ? 'session' : 'tab'}`);

			await page.reload();
			await expect(page.getByText(NOTICE)).toBeVisible({ timeout: 45000 });
			const untouched = (await storedTabs(page)).find((candidate) => candidate.id === tab.id)!;
			expect(untouched.modeStateByMode?.long?.formData?.total_seconds, 'the long setup survives a reload when nothing is done').toBe(40);
			expect(untouched.modeStateByMode?.long?.formData?.piece_prompts).toBe(PIECE_PROMPTS);
			expect(untouched.modeStateByMode?.long?.prompt).toBe(PROMPT);

			await page.getByRole('button', { name: 'Open in Video Director' }).click();
			const converted = await directorTab(page);
			expectConvertedLong(converted, media);

			await expect(page.locator(DIRECTOR)).toBeVisible({ timeout: 30000 });
			await expect(page.locator(DIRECTOR).getByText(/3 shots? · [\d.]+ s/)).toBeVisible({ timeout: 20000 });
			await screenshot(page, JOURNEY, `long-converted-${saved ? 'session' : 'tab'}`);

			const original = (await storedTabs(page)).find((candidate) => candidate.id === tab.id)!;
			expect(original.modeStateByMode?.long?.formData?.total_seconds, 'the original tab still holds the long setup').toBe(40);
			expect(errors).toEqual([]);
		});
	}

	test('a tab stored on Video with the 0.1.0 form opens in Video Director with its values and a usable Generate', async ({ page }) => {
		test.setTimeout(240000);
		const errors = trackPageErrors(page);
		const token = await ownerToken(page);
		const presetId = await prepareHost(page, token);
		const tab = oldTab('old-video-tab', 'Lighthouse video', presetId, 'video', oldVideoForm(), null);

		await openWithTabs(page, [tab], tab.id);

		const director = page.locator(DIRECTOR);
		await expect(director).toBeVisible({ timeout: 45000 });
		await expect(page.getByText(NOTICE)).toHaveCount(0);
		await expect(director.getByText(PROMPT).first()).toBeVisible({ timeout: 20000 });
		await expect(page.getByRole('button', { name: 'Generate', exact: true })).toBeEnabled({ timeout: 30000 });
		await screenshot(page, JOURNEY, 'video-old-form');

		await expect
			.poll(async () => JSON.stringify((await storedTabs(page)).find((candidate) => candidate.id === tab.id)?.videoDirector ?? null), {
				timeout: 20000,
				message: 'the old prompt becomes the Director shot prompt'
			})
			.toContain(PROMPT);

		await expect
			.poll(async () => (await storedTabs(page)).find((candidate) => candidate.id === tab.id)?.formData?.seed, { timeout: 20000 })
			.toBe(424242);
		const stored = (await storedTabs(page)).find((candidate) => candidate.id === tab.id)!;
		expect(stored.selectedMode).toBe('video');
		expect(stored.formData.speed_profile).toBe('quality');
		expect(stored.formData.quantity).toBe(2);
		expect(stored.formData.resolution).toBe('1344x768');
		expect(stored.formData.keyframe_pixel_budget).toBe('2');
		expect(stored.prompt).toBe(PROMPT);
		expect(errors).toEqual([]);
	});

	test('Reuse settings on a generation made in Long video lands in Video Director with the setup carried over', async ({ page }) => {
		test.setTimeout(240000);
		const errors = trackPageErrors(page);
		const token = await ownerToken(page);
		const presetId = await prepareHost(page, token);
		const media = await uploadMedia(page, token);
		const now = new Date().toISOString();
		const item = {
			id: '01E2ERETIREDLONG0000000001',
			preset_id: presetId,
			preset_name: PRESET_NAME,
			mode: 'long',
			form_name: null,
			form_data: oldLongForm(media),
			prompt_state: { prompt: PROMPT, negativePrompt: '', promptSegments: [{ type: 'content', content: PROMPT, chips: {}, enabled: true }] },
			seed: 777,
			status: 'completed',
			progress: 1,
			created_at: now,
			completed_at: now,
			updated_at: now,
			rating: 0,
			is_favorite: false,
			tags: [],
			files: [
				{
					id: 1,
					file_path: 'e2e/long-video.mp4',
					file_type: 'video',
					is_final: true,
					created_at: now,
					width: 832,
					height: 480,
					duration_seconds: 40,
					fps: 24
				}
			]
		};
		await page.route('**/api/generations/history**', async (route) => {
			const path = new URL(route.request().url()).pathname;
			if (path === '/api/generations/history') {
				await route.fulfill({ json: { success: true, data: { generations: [item], total: 1 } } });
				return;
			}
			if (path === `/api/generations/history/${item.id}`) {
				await route.fulfill({ json: { success: true, data: item } });
				return;
			}
			await route.fallback();
		});

		await page.goto('/history');
		const actions = page.locator('[data-generation-card]').getByRole('button', { name: 'Item actions' });
		await expect(actions.first()).toBeVisible({ timeout: 30000 });
		await actions.first().click({ force: true });
		await page.getByRole('menuitem', { name: 'Reuse settings' }).click();

		await page.waitForURL(/\/generate/, { timeout: 30000 });
		await expect(page.locator(DIRECTOR)).toBeVisible({ timeout: 45000 });
		await expect(page.locator(DIRECTOR).getByText(/3 shots? · [\d.]+ s/)).toBeVisible({ timeout: 20000 });
		await screenshot(page, JOURNEY, 'history-reuse-long');

		let reused: Tab | undefined;
		await expect
			.poll(
				async () => {
					reused = (await storedTabs(page)).find((tab) => tab.selectedMode === 'video' && String(tab.name).startsWith('Reused'));
					return !!reused;
				},
				{ timeout: 20000, message: 'Reuse settings should open a tab on Video Director' }
			)
			.toBe(true);
		expectConvertedLong(reused!, media);
		expect(errors).toEqual([]);
	});
});
