import { expect, test, type BrowserContext } from '@playwright/test';
import { solidPng } from '../e2e/mediaToolMocks';
import { EFFECT_CATALOG } from '../component/fixtures/effectCatalog';
import { makeClip, serveRanged } from './fixtures';
import { discoverPreset, generationTab, runScenario, seedTabsState, seekMetrics, type PresetHandle } from './perfKit';

const PASSES = 2;
const MOVES = 120;
const FRAME = solidPng(160, 90);
const POSTER = solidPng(320, 180);
const SOURCE = {
	path: 'uploads/perf-clip.mp4',
	relative_path: 'uploads/perf-clip.mp4',
	url: '/api/e2e-media/clip.mp4',
	name: 'perf-clip.mp4',
	type: 'video',
	item_id: 'item-perf'
};

function editStack() {
	const steps: Array<Record<string, unknown>> = [
		{ id: 's1', tool: 'trim', icon: 'editor-effects', label: 'Trim', summary: '', params: { start: 0.5, end: 9.5 }, hidden: false }
	];
	for (let index = 2; index <= 10; index++) {
		steps.push({
			id: `s${index}`,
			tool: 'film_grain',
			icon: 'editor-effects',
			label: 'Film grain',
			summary: '',
			params: { amount: 10 + index * 6, size: 1 + (index % 3), color: index % 2 === 0, seed: index },
			hidden: false
		});
	}
	return { v: 1, steps, cursor: steps.length, pending: null, seq: steps.length };
}

async function mockMedia(context: BrowserContext, clip: string) {
	await serveRanged(context, '**/api/e2e-media/clip.mp4', clip, 'video/mp4');
	await context.route('**/api/e2e-media/*.png', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: route.request().url().includes('poster') ? POSTER : FRAME })
	);
	await context.route(/\/api\/media-tools(\?.*)?$/, (route) =>
		route.request().method() === 'GET' ? route.fulfill({ json: { success: true, data: { tools: EFFECT_CATALOG } } }) : route.fallback()
	);
	await context.route('**/api/media-tools/**', (route) =>
		new URL(route.request().url()).pathname.endsWith('/preview/frame')
			? route.fulfill({ status: 200, contentType: 'image/png', body: FRAME })
			: route.fallback()
	);
	await context.route(/\/api\/library\/items\/[^/?]+(\?.*)?$/, (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		const id = new URL(route.request().url()).pathname.split('/').pop() ?? 'item';
		return route.fulfill({
			json: {
				success: true,
				data: {
					id,
					filename: `${id}.mp4`,
					original_filename: `${id}.mp4`,
					media_type: 'video',
					mime_type: 'video/mp4',
					url: '/api/e2e-media/clip.mp4',
					thumbnail_medium: '/api/e2e-media/poster.png',
					width: 1920,
					height: 1080,
					duration_seconds: 10,
					tags: []
				}
			}
		});
	});
}

test('video-scrub', async ({ browser }) => {
	let clip = '';
	await runScenario<PresetHandle>(browser, {
		name: 'video-scrub',
		setup: async (b) => {
			clip = makeClip();
			return discoverPreset(b);
		},
		configure: async (context, handle) => {
			await mockMedia(context, clip);
			const generation = generationTab(handle, { id: 'perf-gen-1', name: 'Perf', prompt: '' });
			const tool = {
				id: 'video-tab-1',
				kind: 'tool',
				tool: 'video',
				name: 'perf-clip',
				color: null,
				source: SOURCE,
				state: {
					v: 1,
					stack: editStack(),
					tool: 'trim',
					playhead: 0.5,
					loop: false,
					savedSignature: '[]',
					saved: null,
					renderSignature: null,
					renderMode: null,
					renderStartedAt: null
				},
				run: null,
				activeTool: 'trim',
				dirty: 0
			};
			await seedTabsState(context, handle.tabsKey, { tabs: [generation, tool], activeTabId: tool.id, lastGenerationTabId: 'perf-gen-1' });
		},
		prepare: async (kit) => {
			await kit.page.goto('/generate');
			const editor = kit.page.getByTestId('video-editor');
			await expect(editor).toHaveAttribute('data-ready', 'true', { timeout: 30000 });
			await expect(kit.page.locator('[data-scrub-track]').first()).toBeVisible();
			await kit.page.waitForLoadState('networkidle');
			await kit.page.waitForTimeout(1000);
		},
		measure: async (kit) => {
			const track = (await kit.page.locator('[data-scrub-track]').first().boundingBox())!;
			const y = track.y + track.height / 2;
			const left = track.x + 3;
			const span = track.width - 6;
			await kit.page.mouse.move(left, y);
			const { raw, metrics } = await kit.window(async () => {
				await kit.page.mouse.down();
				for (let pass = 0; pass < PASSES; pass++) {
					for (let i = 1; i <= MOVES; i++) {
						const fraction = pass % 2 === 0 ? i / MOVES : 1 - i / MOVES;
						await kit.page.mouse.move(left + span * fraction, y);
					}
				}
				await kit.page.mouse.up();
				await kit.page.waitForTimeout(500);
			});
			const seeks = seekMetrics(raw);
			if (seeks.seek_count < MOVES / 4) throw new Error(`only ${seeks.seek_count} seeks for ${PASSES * MOVES} scrub moves; the timeline did not drive the video`);
			return { ...metrics, ...seeks };
		}
	});
});
