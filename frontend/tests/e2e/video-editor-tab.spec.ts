import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, scopedStorageKey, screenshot } from './helpers';
import { solidPng } from './mediaToolMocks';

const JOURNEY = 'video-editor-tab';
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

type Json = Record<string, unknown>;

interface Mocks {
	frames: Json[];
	clips: Json[];
	runs: Json[];
	cancels: string[];
	artifacts: number;
	runState: Record<string, { status: string; progress: number }>;
}

function bookTab(page: Page, name: string | RegExp) {
	return page.locator('button.book-tab', { hasText: name });
}

async function json(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

function runBody(id: string, state: { status: string; progress: number }) {
	const items =
		state.status === 'completed'
			? [{ id: `saved-${id}`, filename: `observatory-timelapse-edit-${id}.mp4`, original_filename: 'observatory-timelapse-edit.mp4', media_type: 'video', url: '/api/e2e-media/saved.webm' }]
			: [];
	return { run_id: id, kind: 'run', status: state.status, progress: state.progress, label: 'Rendering', items, replaced: false, preview: null, error: null };
}

async function mockEditorApi(page: Page): Promise<Mocks> {
	const mocks: Mocks = { frames: [], clips: [], runs: [], cancels: [], artifacts: 0, runState: {} };
	await page.route('**/api/e2e-media/**', (route) =>
		route.request().url().endsWith('.png')
			? route.fulfill({ status: 200, contentType: 'image/png', body: FRAME })
			: route.fulfill({ status: 200, contentType: 'video/webm', body: CLIP })
	);
	await page.route('**/api/media-tools/**', async (route) => {
		const request = route.request();
		const path = new URL(request.url()).pathname;
		const method = request.method();
		if (path.endsWith('/preview/frame')) {
			mocks.frames.push(request.postDataJSON() as Json);
			return route.fulfill({ status: 200, contentType: 'image/png', body: FRAME });
		}
		if (path.endsWith('/preview/clip')) {
			mocks.clips.push(request.postDataJSON() as Json);
			return json(route, { run_id: null, preview: { id: 'p1', url: '/api/e2e-media/preview.webm', mime_type: 'video/webm', duration_seconds: 3, width: 854, height: 480 } }, 202);
		}
		if (path.endsWith('/artifacts')) {
			mocks.artifacts += 1;
			return json(route, { id: 'artifact-frame', media_type: 'image', mime_type: 'image/png', url: '/api/e2e-media/frame.png', width: 160, height: 90, duration_seconds: null, fps: null, size: 100 });
		}
		if (path.endsWith('/runs') && method === 'POST') {
			mocks.runs.push(request.postDataJSON() as Json);
			const id = `run-${mocks.runs.length}`;
			mocks.runState[id] ??= { status: 'running', progress: 0.42 };
			return json(route, { run_id: id }, 202);
		}
		if (path.endsWith('/runs') && method === 'GET') {
			return json(route, { runs: Object.entries(mocks.runState).map(([id, state]) => runBody(id, state)) });
		}
		const match = /\/runs\/([^/]+)$/.exec(path);
		if (match) {
			const id = match[1];
			const state = (mocks.runState[id] ??= { status: 'running', progress: 0.42 });
			if (method === 'DELETE') {
				mocks.cancels.push(id);
				state.status = 'cancelled';
			}
			return json(route, runBody(id, state));
		}
		return route.fallback();
	});
	return mocks;
}

async function openEditor(page: Page) {
	await loginAsOwner(page);
	await page.goto('/generate');
	await expect(page.locator('.tab-bar-container:visible, .studio-topbar:visible').first()).toBeVisible({ timeout: 15000 });
	const key = await scopedStorageKey(page, 'potionui_tabs_state');
	await page.evaluate(
		({ k, source }) => {
			const saved = JSON.parse(localStorage.getItem(k) ?? 'null') ?? { tabs: [], activeTabId: '' };
			const generation = saved.tabs.find((tab: { kind?: string }) => tab.kind !== 'tool');
			const tool = { id: 'video-tab-1', kind: 'tool', tool: 'video', name: 'observatory-timelapse', color: null, source, state: null, run: null, activeTool: 'trim', dirty: 0 };
			saved.tabs = [...saved.tabs.filter((tab: { kind?: string }) => tab.kind !== 'tool'), tool];
			saved.activeTabId = tool.id;
			if (generation) saved.lastGenerationTabId = generation.id;
			localStorage.setItem(k, JSON.stringify(saved));
		},
		{ k: key, source: SOURCE }
	);
	await page.reload();
	const editor = page.getByTestId('video-editor');
	await expect(editor).toHaveAttribute('data-ready', 'true', { timeout: 15000 });
	return editor;
}

async function trackGeometry(page: Page) {
	const track = page.locator('[data-scrub-track]');
	const box = (await track.boundingBox())!;
	const duration = Number(await track.getAttribute('aria-valuemax'));
	return { track, box, duration, x: (seconds: number) => box.x + (seconds / duration) * box.width, y: box.y + box.height / 2 };
}

const seconds = (text: string | null) => {
	const [m, s] = (text ?? '0:0').split(':');
	return Number(m) * 60 + Number(s);
};

test.describe('the Video editor tab on desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('trims, previews effects, saves as one run with progress and Cancel, reattaches after a reload and hands a frame to the Image editor', async ({ page }) => {
		const mocks = await mockEditorApi(page);
		const editor = await openEditor(page);
		await expect(bookTab(page, 'observatory-timelapse')).toBeVisible();
		await expect(editor.locator('[data-tool-rail="rail"] [data-tool="trim"]')).toHaveAttribute('aria-pressed', 'true');
		await expect(editor.locator('[data-scrub-frame]').first()).toBeVisible({ timeout: 10000 });

		const geo = await trackGeometry(page);
		expect(geo.duration).toBeGreaterThan(0.5);
		const inHandle = editor.locator('[data-scrub-handle="in"]');
		const inBox = (await inHandle.boundingBox())!;
		await page.mouse.move(inBox.x + inBox.width / 2, geo.y);
		await page.mouse.down();
		await page.mouse.move(geo.x(geo.duration * 0.2), geo.y, { steps: 4 });
		await page.mouse.up();
		const dragged = seconds(await editor.locator('[data-scrub-in]').textContent());
		expect(dragged).toBeGreaterThan(geo.duration * 0.15);
		expect(dragged).toBeLessThan(geo.duration * 0.25);

		await page.mouse.click(geo.x(geo.duration * 0.75), geo.y + 2);
		await page.keyboard.press('o');
		const out = seconds(await editor.locator('[data-scrub-out]').textContent());
		expect(Math.abs(out - geo.duration * 0.75)).toBeLessThan(geo.duration * 0.06);
		await page.mouse.click(geo.x(geo.duration * 0.3), geo.y + 2);
		await page.keyboard.press('i');
		const inPoint = seconds(await editor.locator('[data-scrub-in]').textContent());
		expect(Math.abs(inPoint - geo.duration * 0.3)).toBeLessThan(geo.duration * 0.06);
		await expect(editor.getByRole('button', { name: /Trim, pending/ })).toBeVisible();
		await screenshot(page, JOURNEY, 'trim-1440');
		await page.keyboard.press('Enter');
		await expect(editor.getByRole('button', { name: /Reopen Trim/ })).toBeVisible();

		await editor.locator('[data-tool="effects"]').click();
		await expect(editor.locator('[data-effect-card="film_grain"][data-open]')).toBeVisible();
		await expect(editor.locator('[data-effect-later]')).toContainText('Later');
		await editor.locator('[data-options-apply]').click();
		await expect.poll(() => mocks.frames.length, { timeout: 5000 }).toBeGreaterThan(0);
		const frameRequest = mocks.frames[mocks.frames.length - 1] as { steps: Array<{ tool: string }>; time_seconds: number; input: Json };
		expect(frameRequest.input).toEqual({ item_id: 'item-1' });
		expect(frameRequest.steps.map((step) => step.tool)).toEqual(['trim', 'film_grain']);
		expect(typeof frameRequest.time_seconds).toBe('number');
		await expect(editor.locator('[data-video-frame-preview]')).toBeVisible();

		await page.keyboard.press('\\');
		await expect.poll(() => mocks.frames.some((body) => (body.steps as Array<{ tool: string }>).every((step) => step.tool !== 'film_grain'))).toBe(true);
		await expect(editor.locator('[data-canvas-divider]')).toBeVisible();
		await screenshot(page, JOURNEY, 'effects-compare-1440');
		await page.keyboard.press('\\');

		await editor.locator('[data-preview-clip]').click();
		await expect(editor.locator('[data-video-clip-preview]')).toBeVisible();
		expect(mocks.clips).toHaveLength(1);
		expect(mocks.clips[0]).toMatchObject({ input: { item_id: 'item-1' }, duration_seconds: 3, height: 480 });
		expect((mocks.clips[0].steps as Array<{ tool: string }>).map((step) => step.tool)).toEqual(['trim', 'film_grain']);
		await editor.locator('[data-close-clip-preview]').click();
		await expect(editor.locator('[data-video-clip-preview]')).toHaveCount(0);

		await editor.locator('[data-frame-save]').click();
		const saving = editor.locator('[data-frame-saving]');
		await expect(saving).toContainText('42%');
		await expect(editor.locator('[data-render-card="panel"]')).toContainText('Saving as new');
		await expect(bookTab(page, 'observatory-timelapse').getByTestId('tab-progress')).toHaveText('42%');
		await screenshot(page, JOURNEY, 'save-render-1440');
		expect(mocks.runs[0]).toMatchObject({ inputs: [{ item_id: 'item-1' }], output: { mode: 'new' } });
		const firstSteps = (mocks.runs[0].steps as Array<{ tool: string; params: Json }>);
		expect(firstSteps.map((step) => step.tool)).toEqual(['trim', 'film_grain']);
		expect(firstSteps[0].params.start).toBeCloseTo(inPoint, 1);

		await editor.locator('[data-frame-cancel-save]').click();
		await expect(saving).toHaveCount(0);
		expect(mocks.cancels).toEqual(['run-1']);
		await expect(editor.getByRole('button', { name: /Reopen (Trim|Film grain)/ })).toHaveCount(2);
		await expect(editor.locator('[data-frame-save]')).toHaveText(/Save as new/);

		mocks.runState['run-2'] = { status: 'running', progress: 0.6 };
		await editor.locator('[data-frame-save]').click();
		await expect(saving).toContainText('60%');
		mocks.runState['run-2'] = { status: 'completed', progress: 1 };
		await expect(saving).toHaveCount(0, { timeout: 5000 });
		expect(mocks.runs[1]).toEqual(mocks.runs[0]);
		await expect(editor.locator('[data-frame-save]')).toHaveText(/^\s*Save\s*$/);
		await expect(bookTab(page, 'observatory-timelapse').getByTestId('tab-unsaved')).toHaveCount(0);

		await editor.locator('[data-tool="mute"]').click();
		await editor.locator('[data-options-apply]').click();
		await expect(bookTab(page, 'observatory-timelapse').getByTestId('tab-unsaved')).toBeVisible();
		mocks.runState['run-3'] = { status: 'running', progress: 0.3 };
		await editor.locator('[data-frame-save]').click();
		await expect(saving).toContainText('30%');
		expect(mocks.runs[2]).toMatchObject({ output: { mode: 'update', target_item_id: 'saved-run-2' } });
		expect((mocks.runs[2].steps as Array<{ tool: string }>).map((step) => step.tool)).toEqual(['trim', 'film_grain', 'mute']);

		await page.reload();
		const again = page.getByTestId('video-editor');
		await expect(again).toHaveAttribute('data-ready', 'true', { timeout: 15000 });
		await expect(again.locator('[data-frame-saving]')).toContainText('30%');
		await expect(again.locator('[data-render-card="panel"]')).toBeVisible();
		mocks.runState['run-3'] = { status: 'completed', progress: 1 };
		await expect(again.locator('[data-frame-saving]')).toHaveCount(0, { timeout: 5000 });
		await expect(bookTab(page, 'observatory-timelapse').getByTestId('tab-unsaved')).toHaveCount(0);

		await again.locator('[data-tool="frame"]').click();
		await again.locator('[data-frame-action="open"]').click();
		await expect(page.getByTestId('tool-tab-host')).toHaveAttribute('data-tool', 'image', { timeout: 5000 });
		expect(mocks.artifacts).toBe(1);
		expect((mocks.frames[mocks.frames.length - 1] as Json).format).toBe('png');
		await expect(bookTab(page, /observatory-timelapse-frame/)).toBeVisible();
	});
	test('previews crop, speed and mute live in the browser and saves a split as new items', async ({ page }) => {
		const mocks = await mockEditorApi(page);
		const editor = await openEditor(page);
		const media = editor.locator('[data-video-element]');

		await editor.locator('[data-tool="crop"]').click();
		await editor.getByRole('button', { name: 'Rotate right' }).click();
		await expect(media).toHaveAttribute('style', /rotate\(90deg\)/);
		await expect(editor.locator('[data-crop-rect]')).toBeVisible();
		await editor.locator('[data-options-body]').getByRole('button', { name: '1:1', exact: true }).click();
		const cropBox = (await editor.locator('[data-crop-rect]').boundingBox())!;
		expect(Math.abs(cropBox.width - cropBox.height)).toBeLessThan(3);
		await screenshot(page, JOURNEY, 'crop-1440');
		await editor.locator('[data-options-apply]').click();
		await expect(editor.getByRole('button', { name: /Reopen Crop/ })).toBeVisible();
		await expect(editor.locator('[data-crop-rect]')).toHaveCount(0);

		await editor.locator('[data-tool="speed"]').click();
		await editor.locator('[data-options-apply]').click();
		await expect.poll(() => media.evaluate((el) => (el as HTMLVideoElement).playbackRate)).toBe(2);
		await editor.locator('[data-tool="mute"]').click();
		await editor.locator('[data-options-apply]').click();
		await expect.poll(() => media.evaluate((el) => (el as HTMLVideoElement).muted)).toBe(true);

		const geo = await trackGeometry(page);
		await page.mouse.click(geo.x(geo.duration * 0.5), geo.y + 2);
		await editor.locator('[data-tool="split"]').click();
		await editor.locator('[data-split-playhead]').click();
		await editor.locator('[data-options-apply]').click();
		await expect(editor.getByRole('button', { name: /Reopen Split/ })).toBeVisible();

		await editor.locator('[data-frame-save-menu]').click();
		await expect(page.getByRole('menuitem', { name: /Replace original/ })).toBeDisabled();
		await page.getByRole('menuitem', { name: /Save as new/ }).click();
		await expect(editor.locator('[data-frame-saving]')).toBeVisible();
		const steps = mocks.runs[0].steps as Array<{ tool: string; params: Json }>;
		expect(steps.map((step) => step.tool)).toEqual(['rotate', 'crop', 'speed', 'mute', 'split']);
		expect(steps[0].params).toEqual({ degrees: 90 });
		expect(steps[2].params).toEqual({ speed: 2, keep_pitch: true });
		expect(steps[4].params.mode).toBe('at');
		expect(Number(steps[4].params.at)).toBeCloseTo(geo.duration * 0.25, 1);
		expect(mocks.runs[0].output).toEqual({ mode: 'new' });
	});
});

test.describe('the Video editor tab on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

	test('shows the compact scrub bar, the tool strip under its options, and the render card while saving', async ({ page }) => {
		const mocks = await mockEditorApi(page);
		const editor = await openEditor(page);
		await expect(editor.locator('[data-scrub-bar="compact"]')).toBeVisible();
		await expect(editor.locator('[data-tool-rail="strip"]')).toBeVisible();
		await expect(editor.locator('[data-options-panel="phone"]')).toBeVisible();
		await expect(editor.locator('[data-trim-field="in"]')).toBeVisible();
		const optionsBox = (await editor.locator('[data-options-panel="phone"]').boundingBox())!;
		const stripBox = (await editor.locator('[data-tool-rail="strip"]').boundingBox())!;
		expect(optionsBox.y).toBeLessThan(stripBox.y);
		await expect(editor.locator('[data-scrub-frame]').first()).toBeVisible({ timeout: 10000 });
		const overflowX = await page.evaluate(() => document.scrollingElement!.scrollWidth - document.scrollingElement!.clientWidth);
		expect(overflowX).toBeLessThanOrEqual(0);
		await screenshot(page, JOURNEY, 'phone-trim-390');

		await editor.locator('[data-tool="mute"]').click();
		await editor.locator('[data-options-apply]').click();
		const phone = page.getByTestId('phone-tool-tab');
		await phone.getByRole('button', { name: 'Save', exact: true }).click();
		await page.getByRole('dialog', { name: 'Save' }).getByRole('menuitem', { name: /Save as new/ }).click();
		await expect(editor.locator('[data-render-card="phone"]')).toBeVisible();
		await expect(editor.locator('[data-options-panel="phone"]')).toHaveCount(0);
		await expect(phone.getByTestId('phone-save-progress')).toHaveText('42%');
		await screenshot(page, JOURNEY, 'phone-saving-390');
		await editor.locator('[data-render-cancel]').click();
		await expect(editor.locator('[data-options-panel="phone"]')).toBeVisible();
		expect(mocks.cancels).toEqual(['run-1']);
	});
});
