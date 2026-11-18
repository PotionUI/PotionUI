import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test, expect, type Page, type Route } from '@playwright/test';
import { clearEdits, closeEditorMenu, editorMenu, editorMenuItem, loginAsOwner, ownerToken, scopedStorageKey, screenshot } from './helpers';
import { solidPng } from './mediaToolMocks';
import { EFFECT_CATALOG } from '../component/fixtures/effectCatalog';

const JOURNEY = 'video-editor-edits';
const CLIP = readFileSync(fileURLToPath(new URL('./fixtures/preset-cover.webm', import.meta.url)));
const FRAME = solidPng(160, 90);
const POSTER = solidPng(320, 180);
const SOURCE = {
	path: 'uploads/observatory-timelapse.webm',
	relative_path: 'uploads/observatory-timelapse.webm',
	url: '/api/e2e-media/observatory-timelapse.webm',
	name: 'observatory-timelapse.webm',
	type: 'video',
	item_id: 'item-1'
};

type Json = Record<string, unknown>;

const SHAPES: Array<{ name: string; width: number; height: number; duration: number }> = [
	{ name: 'harbor-dawn.mp4', width: 1920, height: 1080, duration: 12.4 },
	{ name: 'street-portrait.mp4', width: 1080, height: 1920, duration: 75 },
	{ name: 'moon-loop.mp4', width: 1000, height: 1000, duration: 6 }
];

async function json(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

async function mockMedia(page: Page) {
	await page.route('**/api/e2e-media/**', (route) =>
		route.request().url().endsWith('.png') ? route.fulfill({ status: 200, contentType: 'image/png', body: route.request().url().includes('poster') ? POSTER : FRAME }) : route.fulfill({ status: 200, contentType: 'video/webm', body: CLIP })
	);
	await page.route(/\/api\/media-tools(\?.*)?$/, (route) => (route.request().method() === 'GET' ? json(route, { tools: EFFECT_CATALOG }) : route.fallback()));
	await page.route('**/api/media-tools/**', (route) => {
		const path = new URL(route.request().url()).pathname;
		if (path.endsWith('/preview/frame')) return route.fulfill({ status: 200, contentType: 'image/png', body: FRAME });
		return route.fallback();
	});
	await page.route(/\/api\/library\/items\/[^/?]+(\?.*)?$/, (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		const id = new URL(route.request().url()).pathname.split('/').pop() ?? 'item';
		return json(route, {
			id,
			filename: `${id}.mp4`,
			original_filename: `${id}.mp4`,
			media_type: 'video',
			mime_type: 'video/mp4',
			url: '/api/e2e-media/observatory-timelapse.webm',
			thumbnail_medium: '/api/e2e-media/poster.png',
			width: 1280,
			height: 720,
			duration_seconds: 6,
			tags: []
		});
	});
}

function seededState(): Json {
	const steps = [
		{ id: 's1', tool: 'trim', icon: 'editor-effects', label: 'Trim', summary: '', params: { start: 0.5, end: 1.5 }, hidden: false },
		{ id: 's2', tool: 'film_grain', icon: 'editor-effects', label: 'Film grain', summary: '', params: { amount: 30, size: 2, color: false, seed: 7 }, hidden: false }
	];
	return {
		v: 1,
		stack: { v: 1, steps, cursor: 2, pending: null, seq: 2 },
		tool: 'trim',
		playhead: 0.8,
		loop: true,
		savedSignature: '[]',
		saved: null,
		renderSignature: null,
		renderMode: null,
		renderStartedAt: null
	};
}

async function seedTab(page: Page, source: Json | null, state: Json | null) {
	await loginAsOwner(page);
	await page.goto('/generate');
	await expect(page.locator('.tab-bar-container:visible, .studio-topbar:visible').first()).toBeVisible({ timeout: 15000 });
	const key = await scopedStorageKey(page, 'potionui_tabs_state');
	await page.addInitScript(
		({ k, source, state }) => {
			if (sessionStorage.getItem('e2e-video-edits-seeded')) return;
			sessionStorage.setItem('e2e-video-edits-seeded', '1');
			const saved = JSON.parse(localStorage.getItem(k) ?? 'null') ?? { tabs: [], activeTabId: '' };
			const generation = saved.tabs.find((tab: { kind?: string }) => tab.kind !== 'tool');
			const tool = { id: 'video-tab-1', kind: 'tool', tool: 'video', name: 'observatory-timelapse', color: null, source, state, run: null, activeTool: state ? 'trim' : null, dirty: 0 };
			saved.tabs = [...saved.tabs.filter((tab: { kind?: string }) => tab.kind !== 'tool'), tool];
			saved.activeTabId = tool.id;
			if (generation) saved.lastGenerationTabId = generation.id;
			localStorage.setItem(k, JSON.stringify(saved));
		},
		{ k: key, source, state }
	);
	await page.reload();
}

async function openEditor(page: Page) {
	await seedTab(page, SOURCE, seededState());
	const editor = page.getByTestId('video-editor');
	await expect(editor).toHaveAttribute('data-ready', 'true', { timeout: 15000 });
	return editor;
}

async function apiEdits(page: Page) {
	const headers = { Authorization: `Bearer ${await ownerToken(page)}` };
	const response = await page.request.get('/api/edits?tool=video&limit=50', { headers });
	expect(response.ok()).toBeTruthy();
	return (await response.json()).data as { edits: Json[]; total: number };
}

async function createClipEdits(page: Page) {
	const headers = { Authorization: `Bearer ${await ownerToken(page)}` };
	for (const [index, shape] of SHAPES.entries()) {
		const response = await page.request.post('/api/edits', {
			headers,
			data: {
				tool: 'video',
				name: index === 0 ? 'Cover cut' : null,
				source: { kind: 'library', id: `item-${index + 1}`, name: shape.name },
				state: { ...seededState(), clip: { duration_seconds: shape.duration, width: shape.width, height: shape.height } }
			}
		});
		expect(response.ok(), await response.text()).toBeTruthy();
	}
}

test.describe('clips are saved as edits', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test.beforeEach(async ({ page }) => {
		await mockMedia(page);
	});

	test('a change saves the clip as an edit, shows Saved and lists it in the File menu', async ({ page }) => {
		await loginAsOwner(page);
		await clearEdits(page);
		const editor = await openEditor(page);
		await expect(page.locator('[data-edit-save-state]')).toHaveCount(0);

		await editor.locator('[data-frame-undo]').first().click();
		await expect(page.locator('[data-edit-save-state="saved"]').first()).toBeVisible({ timeout: 15000 });

		const listed = await apiEdits(page);
		expect(listed.total).toBe(1);
		const edit = listed.edits[0];
		expect(edit.tool).toBe('video');
		expect(edit.source).toMatchObject({ kind: 'library', id: 'item-1', name: 'observatory-timelapse.webm' });
		expect((edit.clip as { duration_seconds: number }).duration_seconds).toBeGreaterThan(0);

		await expect(await editorMenuItem(page, 'File', 'Recent edits', 'observatory-timelapse')).toBeVisible();
		await closeEditorMenu(page);
		await editorMenu(page, 'File', 'See all edits');
		const dialog = page.locator('[data-recent-edits]');
		await expect(dialog.locator('[data-edit-tile]')).toHaveCount(1);
		await expect(dialog.locator('[data-clip-duration]')).toHaveText(/^\d+:\d{2}$/);
	});

	test('the start screen lists clip edits as posters that keep their aspect, and opening one restores it', async ({ page }) => {
		await loginAsOwner(page);
		await clearEdits(page);
		await createClipEdits(page);
		await seedTab(page, null, null);

		const start = page.getByTestId('tool-tab-empty');
		await expect(start).toContainText('Open a clip to edit');
		await expect(start.locator('[data-start-recent]')).toContainText('3');
		await screenshot(page, JOURNEY, '70-edits-start');
		await start.locator('[data-start-recent]').click();

		const dialog = page.locator('[data-recent-edits]');
		const tiles = dialog.locator('[data-edit-tile]');
		await expect(tiles).toHaveCount(3);
		await expect(dialog.locator('[data-clip-duration]')).toHaveText(['0:06', '1:15', '0:12']);
		for (const [index, expected] of [1, 9 / 16, 16 / 9].entries()) {
			const box = (await tiles.nth(index).locator('[data-edit-open]').boundingBox())!;
			expect(box.width / box.height).toBeCloseTo(expected, 1);
		}
		await expect(dialog.locator('[data-edit-open] img').first()).toBeVisible();
		await screenshot(page, JOURNEY, '71-edits-dialog');

		await tiles.filter({ hasText: 'Cover cut' }).locator('[data-edit-open]').click();
		const editor = page.getByTestId('video-editor');
		await expect(editor).toHaveAttribute('data-ready', 'true', { timeout: 15000 });
		await expect(page.locator('[data-edit-save-state="saved"]').first()).toBeVisible({ timeout: 15000 });
		await expect(editor.locator('[data-edit-stack] [data-stack-row="s1"]')).toBeVisible();
		await expect(editor.locator('[data-edit-stack] [data-stack-row="s2"]')).toBeVisible();
		expect((await apiEdits(page)).total).toBe(3);
	});
});
