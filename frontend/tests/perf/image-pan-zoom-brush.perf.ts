import { expect, test, type Page } from '@playwright/test';
import { selectRailTool } from '../e2e/helpers';
import { colourPng, solidPng } from '../e2e/mediaToolMocks';
import { bigImagePng, rng } from './fixtures';
import { percentile, runScenario, type Kit, type Metrics } from './perfKit';

const GENERATION_ID = 'gen-perf-4k';
const FILE_ID = 901;
const WIDTH = 3840;
const HEIGHT = 2160;
const SOURCE_FILE = `outputs/2026-11-01/${GENERATION_ID}/perf-4k.png`;
const CANVAS = '[data-paint-stage] canvas[aria-label="Drawing canvas"]';
const ZOOM_STEPS = 10;
const WHEEL_PAIRS = 20;
const PANS = 40;
const STROKES = 100;
const STROKE_MOVES = 12;
const ADJUST_MOVES = 240;

const FORM_SCHEMA = {
	properties: {
		generation: {
			type: 'group',
			title: 'Generation',
			children: [
				{ name: 'prompt', type: 'string', title: 'Prompt', default: 'an observatory at dusk' },
				{ name: 'image', type: 'image', title: 'Reference image', allow_inpaint: true }
			]
		}
	}
};

const ok = (data: unknown, status = 200) => ({ status, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });

async function mockEditor(page: Page, source: Buffer) {
	const small = solidPng(64, 64);
	const now = new Date().toISOString();
	const generation = {
		id: GENERATION_ID,
		preset_id: 'krea2',
		preset_name: 'Krea 2',
		mode: 'txt2img',
		form_data: {},
		status: 'completed',
		progress: 1,
		created_at: now,
		updated_at: now,
		completed_at: now,
		files: [{ id: FILE_ID, file_path: SOURCE_FILE, file_type: 'image', is_final: true, created_at: now, width: WIDTH, height: HEIGHT }]
	};
	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) =>
		route.request().method() === 'GET' ? route.fulfill(ok({ generations: [generation], total: 1 })) : route.fallback()
	);
	await page.route(`**/api/media/generations/${GENERATION_ID}/perf-4k.png*`, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: source }));
	await page.route('**/api/e2e-art/**', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: small }));
	await page.route(/\/api\/media-tools\/artifacts$/, (route) =>
		route.fulfill(ok({ id: 'art-1', media_type: 'image', mime_type: 'image/png', url: '/api/e2e-art/art-1.png', width: 480, height: 480, duration_seconds: null, fps: null, size: 2048 }))
	);
	await page.route(/\/api\/media-tools\/preview\/frame$/, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: colourPng(96, 64, [90, 110, 150]) }));
	await page.route(/\/api\/media\/upload$/, (route) =>
		route.fulfill(ok({ path: 'uploads/sent-1.png', relative_path: 'uploads/sent-1.png', filename: 'sent-1.png', url: '/api/e2e-art/sent-1.png', width: 480, height: 480, size: 4096 }))
	);
	await page.route('**/api/presets/*/form*', (route) => route.fulfill(ok({ preset_id: 'mock', form_schema: FORM_SCHEMA })));
}

async function openPaintOnImage(page: Page) {
	await page.goto('/generate');
	await expect(page.locator('.tab-bar-container')).toBeVisible({ timeout: 30000 });
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await page.getByRole('menu', { name: 'New tab' }).getByRole('menuitem', { name: /Image editor/ }).click();
	const empty = page.getByTestId('tool-tab-empty');
	await expect(empty.locator('[data-start-source="history"]')).toBeVisible();
	await empty.locator('[data-start-source="history"]').click();
	await page.getByTestId('history-file-tile').first().locator('[data-pick-main]').click();
	await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'crop', { timeout: 60000 });
	await selectRailTool(page, 'paint.brush');
	await expect(page.locator(CANVAS)).toBeVisible({ timeout: 30000 });
	await expect(page.locator('[data-paint-stage][data-paint-ready]')).toBeVisible({ timeout: 60000 });
}

const docBox = (page: Page) => page.locator(CANVAS).getAttribute('data-doc-box').then((value) => (value ?? '').split(',').map(Number));

function canvasHash(page: Page): Promise<string> {
	return page.locator(CANVAS).evaluate((node) => {
		const canvas = node as HTMLCanvasElement;
		const data = canvas.getContext('2d')!.getImageData(0, 0, canvas.width, canvas.height).data;
		let hash = 2166136261;
		for (let i = 0; i < data.length; i += 7) hash = Math.imul(hash ^ data[i], 16777619) >>> 0;
		return String(hash);
	});
}

function prefixed(prefix: string, metrics: Metrics, keys: string[]): Metrics {
	const out: Metrics = {};
	for (const key of keys) if (key in metrics) out[`${prefix}_${key}`] = metrics[key];
	return out;
}

const WINDOW_KEYS = ['wall_ms', 'frame_p50_ms', 'frame_p95_ms', 'dropped_frames', 'long_task_total_ms', 'tbt_ms', 'script_ms', 'script_ms_per_frame', 'mutations_per_frame'];

async function adjustWindow(kit: Kit): Promise<Metrics> {
	const rail = kit.page.locator('[data-tool-rail] button[data-tool="vignette"], [data-tool-rail] button[data-group-members~="vignette"]').first();
	if (!(await rail.count())) throw new Error('the vignette entry is missing from the tool rail; the Adjust window cannot run');
	await selectRailTool(kit.page, 'vignette');
	const slider = kit.page.locator('[data-entry-options="vignette"] input[type="range"]').first();
	await slider.waitFor({ state: 'visible', timeout: 15000 });
	await kit.page.waitForTimeout(500);
	const box = (await slider.boundingBox())!;
	const y = box.y + box.height / 2;
	const left = box.x + 4;
	const span = box.width - 8;
	await kit.page.mouse.move(left + span / 2, y);
	const valueBefore = await slider.inputValue();
	const { metrics } = await kit.window(async () => {
		await kit.page.mouse.down();
		for (let i = 0; i < ADJUST_MOVES; i++) {
			const phase = (i % 80) / 80;
			await kit.page.mouse.move(left + span * (phase < 0.5 ? phase * 2 : 2 - phase * 2), y);
		}
		await kit.page.mouse.up();
	});
	if ((await slider.inputValue()) === valueBefore) throw new Error('the Adjust slider did not change; the drag did not land');
	return prefixed('adjust', metrics, WINDOW_KEYS);
}

test('image-pan-zoom-brush', async ({ browser }) => {
	const source = bigImagePng(WIDTH, HEIGHT);
	await runScenario<null>(browser, {
		name: 'image-pan-zoom-brush',
		prepare: async (kit) => {
			await mockEditor(kit.page, source);
			await openPaintOnImage(kit.page);
			await kit.page.waitForTimeout(1000);
		},
		measure: async (kit) => {
			const page = kit.page;
			const area = (await page.locator(CANVAS).boundingBox())!;
			const cx = area.x + area.width / 2;
			const cy = area.y + area.height / 2;

			await page.mouse.move(cx, cy);
			const boxStart = await docBox(page);
			let boxZoomed: number[] = boxStart;
			let boxPanned: number[] = boxStart;
			const pan = await kit.window(async () => {
				for (let i = 0; i < ZOOM_STEPS; i++) await page.mouse.wheel(0, -120);
				boxZoomed = await docBox(page);
				for (let i = 0; i < PANS; i++) {
					const direction = i % 4 === 3 ? -1 : 1;
					await page.mouse.move(cx, cy);
					await page.mouse.down({ button: 'middle' });
					for (let step = 1; step <= 6; step++) await page.mouse.move(cx + direction * step * 12, cy + direction * step * 6);
					await page.mouse.up({ button: 'middle' });
				}
				boxPanned = await docBox(page);
				for (let i = 0; i < ZOOM_STEPS; i++) await page.mouse.wheel(0, 120);
				for (let i = 0; i < WHEEL_PAIRS; i++) {
					await page.mouse.wheel(0, -120);
					await page.mouse.wheel(0, 120);
				}
			});
			if (!(boxZoomed[2] > boxStart[2] * 1.2)) throw new Error(`wheel zoom did not enlarge the document: ${boxStart.join(',')} -> ${boxZoomed.join(',')}`);
			if (boxPanned[0] === boxZoomed[0] && boxPanned[1] === boxZoomed[1]) throw new Error(`panning did not move the document: ${boxZoomed.join(',')}`);
			const hashBefore = await canvasHash(page);

			const random = rng(7);
			const strokeTimes: number[] = [];
			const brush = await kit.window(async () => {
				for (let stroke = 0; stroke < STROKES; stroke++) {
					const x = area.x + area.width * (0.15 + 0.7 * random());
					const y = area.y + area.height * (0.15 + 0.7 * random());
					const started = performance.now();
					await page.mouse.move(x, y);
					await page.mouse.down();
					for (let step = 1; step <= STROKE_MOVES; step++) await page.mouse.move(x + step * 14 * (random() < 0.5 ? 1 : 0.5), y + step * 5 * (random() - 0.4));
					await page.mouse.up();
					strokeTimes.push(performance.now() - started);
				}
			});
			if ((await canvasHash(page)) === hashBefore) throw new Error('the brush strokes did not change the canvas pixels');
			const heap = await kit.heapMb();
			const adjust = await adjustWindow(kit);
			return {
				...prefixed('pan', pan.metrics, WINDOW_KEYS),
				...prefixed('brush', brush.metrics, WINDOW_KEYS),
				brush_stroke_p95_ms: percentile(strokeTimes, 0.95),
				heap_used_mb: heap,
				...adjust
			};
		}
	});
});
