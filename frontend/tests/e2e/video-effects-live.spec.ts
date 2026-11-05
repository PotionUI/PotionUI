import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, scopedStorageKey, screenshot } from './helpers';
import { solidPng } from './mediaToolMocks';
import { EFFECT_CATALOG, SHADERS } from '../component/fixtures/effectCatalog';

const JOURNEY = 'video-effects-live';
const CLIP = readFileSync(fileURLToPath(new URL('./fixtures/preset-cover.webm', import.meta.url)));
const FRAME = solidPng(160, 90);
const SOURCE = {
	path: 'uploads/observatory-timelapse.webm',
	relative_path: 'uploads/observatory-timelapse.webm',
	url: '/api/e2e-media/observatory-timelapse.webm',
	name: 'observatory-timelapse.webm',
	type: 'video',
	item_id: 'item-1'
};
const { DISPLAY: _display, WAYLAND_DISPLAY: _wayland, ...browserEnv } = process.env;

test.use({ launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'], env: browserEnv as Record<string, string> } });

type Json = Record<string, unknown>;

interface Mocks {
	frames: Json[];
	clips: Json[];
	shaders: string[];
	analysis: Json[];
}

async function json(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

async function mockApi(page: Page): Promise<Mocks> {
	const mocks: Mocks = { frames: [], clips: [], shaders: [], analysis: [] };
	await page.route('**/api/e2e-media/**', (route) => route.fulfill({ status: 200, contentType: 'video/webm', body: CLIP }));
	await page.route(/\/api\/media-tools(\?.*)?$/, (route) => (route.request().method() === 'GET' ? json(route, { tools: EFFECT_CATALOG }) : route.fallback()));
	await page.route('**/api/media-tools/**', async (route) => {
		const request = route.request();
		const path = new URL(request.url()).pathname;
		if (path.endsWith('/shader')) {
			mocks.shaders.push(path);
			return route.fulfill({ status: 200, contentType: 'text/plain', body: SHADERS[path] ?? '' });
		}
		if (path.endsWith('/effects/analysis')) {
			mocks.analysis.push(request.postDataJSON() as Json);
			return json(route, { values: { gain_r: 1.1, gain_g: 1, gain_b: 0.9, black: 0.05, white: 0.95 } });
		}
		if (path.endsWith('/preview/frame')) {
			mocks.frames.push(request.postDataJSON() as Json);
			return route.fulfill({ status: 200, contentType: 'image/png', body: FRAME });
		}
		if (path.endsWith('/preview/clip')) {
			mocks.clips.push(request.postDataJSON() as Json);
			return json(route, { run_id: null, preview: { id: 'p1', url: '/api/e2e-media/preview.webm', mime_type: 'video/webm', duration_seconds: 3, width: 854, height: 480 } }, 202);
		}
		if (path.endsWith('/runs')) return json(route, { runs: [] });
		return route.fallback();
	});
	return mocks;
}

const GRAIN = { tool: 'film_grain', params: { amount: 100, size: 1, color: true, seed: 7 } };

function seeded(steps: Array<{ tool: string; params: Json }>, tool = 'effects'): Json {
	const list = steps.map((step, i) => ({ id: `s${i + 1}`, tool: step.tool, icon: 'editor-effects', label: step.tool, summary: '', params: step.params, hidden: false }));
	return {
		v: 1,
		stack: { v: 1, steps: list, cursor: list.length, pending: null, seq: list.length },
		tool,
		playhead: 0.6,
		loop: true,
		savedSignature: '[]',
		saved: null,
		renderSignature: null,
		renderMode: null,
		renderStartedAt: null
	};
}

async function openEditor(page: Page, state: Json) {
	await loginAsOwner(page);
	await page.goto('/generate');
	await expect(page.locator('.tab-bar-container:visible, .studio-topbar:visible').first()).toBeVisible({ timeout: 15000 });
	const key = await scopedStorageKey(page, 'potionui_tabs_state');
	await page.addInitScript(
		({ k, source, state }) => {
			if (sessionStorage.getItem('e2e-video-seeded')) return;
			sessionStorage.setItem('e2e-video-seeded', '1');
			const saved = JSON.parse(localStorage.getItem(k) ?? 'null') ?? { tabs: [], activeTabId: '' };
			const generation = saved.tabs.find((tab: { kind?: string }) => tab.kind !== 'tool');
			const tool = { id: 'video-tab-1', kind: 'tool', tool: 'video', name: 'observatory-timelapse', color: null, source, state, run: null, activeTool: state.tool, dirty: 0 };
			saved.tabs = [...saved.tabs.filter((tab: { kind?: string }) => tab.kind !== 'tool'), tool];
			saved.activeTabId = tool.id;
			if (generation) saved.lastGenerationTabId = generation.id;
			localStorage.setItem(k, JSON.stringify(saved));
		},
		{ k: key, source: SOURCE, state }
	);
	await page.reload();
	const editor = page.getByTestId('video-editor');
	await expect(editor).toHaveAttribute('data-ready', 'true', { timeout: 15000 });
	return editor;
}

interface Sample {
	width: number;
	canvas: number[];
	video: number[];
}

async function sample(page: Page): Promise<Sample> {
	const shot = await page.locator('[data-live-preview]').screenshot({ animations: 'disabled' });
	return page.evaluate(async (png) => {
		const image = new Image();
		image.src = `data:image/png;base64,${png}`;
		await image.decode();
		const video = document.querySelector<HTMLVideoElement>('[data-video-element]')!;
		const width = video.videoWidth;
		const height = video.videoHeight;
		const read = (source: CanvasImageSource) => {
			const scratch = document.createElement('canvas');
			scratch.width = width;
			scratch.height = height;
			const context = scratch.getContext('2d', { willReadFrequently: true })!;
			context.imageSmoothingEnabled = false;
			context.drawImage(source, 0, 0, width, height);
			const data = context.getImageData(0, 0, width, height).data;
			const out: number[] = [];
			for (let y = Math.round(height * 0.45); y < height - 2; y += 5) for (let x = 2; x < width; x += 5) out.push(data[(y * width + x) * 4], data[(y * width + x) * 4 + 1], data[(y * width + x) * 4 + 2]);
			return out;
		};
		return { width: Math.ceil((width - 2) / 5), canvas: read(image), video: read(video) };
	}, shot.toString('base64'));
}

function meanDiff(a: number[], b: number[], keep: (column: number, columns: number) => boolean = () => true, columns = 1): number {
	let total = 0;
	let count = 0;
	for (let i = 0; i < a.length; i += 1) {
		const column = Math.floor(i / 3) % columns;
		if (!keep(column, columns)) continue;
		total += Math.abs(a[i] - b[i]);
		count += 1;
	}
	return count ? total / count : 0;
}

const brightness = (values: number[]) => values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);

test.describe('live effect preview on desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('plays grain live on the canvas, compares before and after in the canvas and hides Preview 3 s', async ({ page }) => {
		const mocks = await mockApi(page);
		const editor = await openEditor(page, seeded([GRAIN]));
		const canvas = editor.locator('[data-live-preview]');
		await expect(canvas).toBeVisible();
		await expect.poll(() => mocks.shaders, { timeout: 5000 }).toContain('/api/media-tools/effects/film_grain/shader');
		await expect.poll(async () => brightness((await sample(page)).canvas), { timeout: 10000 }).toBeGreaterThan(1);

		const still = await sample(page);
		expect(meanDiff(still.canvas, still.video)).toBeGreaterThan(4);
		await expect(editor.locator('[data-preview-clip]')).toHaveCount(0);
		await expect(editor.locator('[data-effect-card="film_grain"]')).toBeVisible();
		await screenshot(page, JOURNEY, 'effects-live-1440');

		await page.keyboard.press('\\');
		await expect(editor.locator('[data-canvas-divider]')).toBeVisible();
		await expect(editor.locator('[data-canvas-before]')).toHaveCount(0);
		await expect
			.poll(async () => {
				const split = await sample(page);
				const left = meanDiff(split.canvas, split.video, (column, columns) => column < columns * 0.4, split.width);
				const right = meanDiff(split.canvas, split.video, (column, columns) => column > columns * 0.6, split.width);
				return right - left;
			}, { timeout: 5000 })
			.toBeGreaterThan(4);
		await screenshot(page, JOURNEY, 'effects-compare-1440');
		await page.keyboard.press('\\');

		await editor.locator('[data-scrub-play]').click();
		await expect.poll(() => editor.locator('[data-video-element]').evaluate((el) => !(el as HTMLVideoElement).paused)).toBe(true);
		const first = (await sample(page)).canvas;
		await expect.poll(async () => meanDiff((await sample(page)).canvas, first), { timeout: 5000 }).toBeGreaterThan(1);
		await editor.locator('[data-scrub-play]').click();
		expect(mocks.frames).toHaveLength(0);
		expect(mocks.clips).toHaveLength(0);
	});

	test('measures fix colours before it previews, and lists plugin effects with no source label', async ({ page }) => {
		const mocks = await mockApi(page);
		const editor = await openEditor(page, seeded([{ tool: 'trim', params: { start: 0.2, end: 1.8 } }]));
		const panel = editor.locator('[data-options-body]');
		await expect(panel.locator('[data-effect-card]')).toHaveCount(4);
		await expect(panel).not.toContainText(/plugin|retro-tape/i);
		await editor.locator('[data-tray-item="fix_colors"]').click();
		await editor.locator('[data-options-apply]').click();
		await expect.poll(() => mocks.analysis.length, { timeout: 5000 }).toBe(1);
		expect(mocks.analysis[0]).toMatchObject({ input: { item_id: 'item-1' }, step_index: 1 });
		expect((mocks.analysis[0].steps as Array<{ tool: string }>).map((step) => step.tool)).toEqual(['trim', 'fix_colors']);
		await expect(editor.locator('[data-live-preview]')).toBeVisible();
		await expect(editor.locator('[data-live-chips]')).not.toContainText('Measuring');

		await editor.locator('[data-tray-item="retro-tape.wobble"]').click();
		await editor.locator('[data-options-apply]').click();
		await expect(editor.locator('[data-live-chips]')).toContainText('Tape wobble shows when paused');
		await expect(editor.locator('[data-preview-clip]')).toBeVisible();
		await expect.poll(() => mocks.frames.length, { timeout: 5000 }).toBeGreaterThan(0);
	});
});

test.describe('live effect preview fallback', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('uses the server frame and shows Preview 3 s when WebGL2 is missing', async ({ page }) => {
		await page.addInitScript(() => {
			const original = HTMLCanvasElement.prototype.getContext;
			HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, kind: string, ...rest: unknown[]) {
				if (kind === 'webgl2') return null;
				return (original as (...args: unknown[]) => unknown).call(this, kind, ...rest);
			} as typeof original;
		});
		const mocks = await mockApi(page);
		const editor = await openEditor(page, seeded([GRAIN]));
		await expect.poll(() => mocks.frames.length, { timeout: 5000 }).toBeGreaterThan(0);
		await expect(editor.locator('[data-video-frame-preview]')).toBeVisible();
		await expect(editor.locator('[data-live-preview]')).toHaveCount(0);
		const preview = editor.locator('[data-preview-clip]');
		await expect(preview).toContainText('Preview 3 s');
		await screenshot(page, JOURNEY, 'effects-fallback-1440');
		await preview.click();
		await expect(editor.locator('[data-video-clip-preview]')).toBeVisible();
		expect((mocks.clips[0].steps as Array<{ tool: string }>).map((step) => step.tool)).toEqual(['film_grain']);
	});
});

test.describe('the Effects panel on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

	test('picks effects from the strip and previews live', async ({ page }) => {
		await mockApi(page);
		const editor = await openEditor(page, seeded([GRAIN]));
		await expect(editor.locator('[data-effect-strip]')).toBeVisible();
		await expect(editor.locator('[data-effect-chip]')).toHaveCount(4);
		await expect(editor.locator('[data-live-preview]')).toBeVisible();
		await expect(editor.locator('[data-preview-clip]')).toHaveCount(0);
		const overflowX = await page.evaluate(() => document.scrollingElement!.scrollWidth - document.scrollingElement!.clientWidth);
		expect(overflowX).toBeLessThanOrEqual(0);
		await screenshot(page, JOURNEY, 'effects-phone-390');
	});
});
