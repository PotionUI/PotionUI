import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner } from './helpers';

const ID = '01SEAMALIGN000000000000001';
const PNG =
	'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEgAF/QualzQAAAABJRU5ErkJggg==';
const LONG =
	'For the target video, at 0.00 seconds into the target video, <Picture 1> is fully referenced.\n\nintegrated_multimodal_description: [Shot 1] A courier cycles through a rain-washed market street at dawn.';

function generation() {
	const now = new Date().toISOString();
	return {
		id: ID,
		preset_id: 'p/v',
		preset_name: 'MiniMax-H3',
		mode: 'text_to_video',
		form_data: {},
		status: 'completed',
		progress: 1,
		created_at: now,
		completed_at: now,
		updated_at: now,
		run_seconds: 576,
		rating: 3,
		is_favorite: false,
		tags: [],
		segments: [{ prompt_index: 0, segment_index: 0, channel: 'positive', text: 'A courier cycling.' }],
		files: [
			{ id: 1, file_path: 'e2e/seam.mp4', file_type: 'video', is_final: true, created_at: now, width: 640, height: 360, duration_seconds: 2, fps: 24, system_tags: [] }
		]
	};
}

function libraryRow(id: string, name: string) {
	return {
		id,
		filename: `${id}.png`,
		original_filename: name,
		media_type: 'image',
		mime_type: 'image/png',
		url: PNG,
		thumbnail_medium: PNG,
		width: 1216,
		height: 832,
		size: 2411520,
		tags: [{ id: 't1', name: 'portrait' }],
		is_favorite: true,
		created_at: '2026-01-01T00:00:00Z',
		source_generation_id: null
	};
}

async function openHistoryDetails(page: Page) {
	const item = generation();
	await page.route('**/api/generations/**', async (route) => {
		const path = new URL(route.request().url()).pathname;
		if (path === '/api/generations/history') return route.fulfill({ json: { success: true, data: { generations: [item], total: 1 } } });
		if (path === `/api/generations/history/${ID}`) return route.fulfill({ json: { success: true, data: item } });
		if (path === `/api/generations/${ID}/params/0`) {
			return route.fulfill({
				json: { success: true, data: { parameters: { positive_prompt: LONG, duration: 4, fps: 24, resolution: '768x1344', seed: 1117566997, steps: 24 }, models: [] } }
			});
		}
		return route.fallback();
	});
	await loginAsOwner(page);
	await page.goto('/history');
	await page.waitForLoadState('networkidle');
	await page.locator('[data-generation-card]').getByRole('button', { name: 'Item actions' }).first().click({ force: true });
	await page.getByRole('menuitem', { name: 'Open details' }).click();
	await page.waitForSelector('[data-details-info] [data-testid="parameter-block"]');
}

async function openLibraryDetails(page: Page) {
	const rows = [libraryRow('lib-1', 'harbour.png'), libraryRow('lib-2', 'dunes.png')];
	const ok = (data: unknown) => ({ json: { success: true, data } });
	await page.route(/\/api\/library\/items(\?.*)?$/, (route) => route.fulfill(ok({ items: rows, total: 2, limit: 24, offset: 0 })));
	await page.route(/\/api\/collections\/?(\?.*)?$/, (route) => route.fulfill(ok({ collections: [], smart_counts: { all: 2, favorites: 2, unsorted: 2 } })));
	await page.route(/\/api\/library\/facets$/, (route) => route.fulfill(ok({ media_types: { image: 2 } })));
	await page.route(/\/api\/tags(\?.*)?$/, (route) => route.fulfill(ok({ tags: [] })));
	await loginAsOwner(page);
	await page.goto('/library');
	await page.locator('[data-library-card]').first().click();
	await page.waitForSelector('[data-details-info] [data-details-section]');
}

async function measure(page: Page) {
	return page.evaluate(() => {
		const rect = (el: Element | null) => {
			if (!el) return null;
			const b = el.getBoundingClientRect();
			return { x: b.left, y: b.top, bottom: b.bottom, right: b.right, width: b.width, height: b.height };
		};
		const col = document.querySelector('[data-details-info]') as HTMLElement;
		const bar = document.querySelector('[data-details-action-bar]') as HTMLElement;
		const sections = [...col.querySelectorAll('[data-details-section]')] as HTMLElement[];
		const strip = sections[0].firstElementChild as HTMLElement;
		const block = col.querySelector('[data-testid="parameter-block"]');
		const box = col.querySelector('[data-param-cell]');
		return {
			col: rect(col),
			bar: rect(bar),
			strip: rect(strip),
			icon: rect(strip.querySelector('svg')),
			text: rect(strip.querySelector('h3')),
			label: rect(block?.querySelector('span') ?? null),
			textBox: rect(block?.querySelector('div.border') ?? null),
			box: rect(box),
			firstButton: rect(bar.querySelector('button')),
			sections: sections.map((s) => ({ id: s.getAttribute('data-details-section'), rect: rect(s), header: rect(s.firstElementChild) }))
		};
	});
}

for (const [width, height] of [[1440, 900], [390, 844]] as const) {
	test.describe(`details column alignment at ${width}`, () => {
		test.beforeEach(async ({ page }) => {
			await page.setViewportSize({ width, height });
		});

		test('history: the first header strip is the first thing in the column and shares one gutter', async ({ page }) => {
			await openHistoryDetails(page);
			const m = await measure(page);
			expect(m.strip!.y).toBeCloseTo(m.col!.y + (width >= 768 ? 0 : 1), 0);
			if (width >= 768) {
				expect(m.strip!.y).toBeCloseTo(m.bar!.y, 0);
				expect(m.strip!.bottom).toBeCloseTo(m.bar!.bottom, 0);
				expect(m.strip!.height).toBeCloseTo(m.bar!.height, 0);
				const stripCentre = m.text!.y + m.text!.height / 2;
				const buttonCentre = m.firstButton!.y + m.firstButton!.height / 2;
				expect(stripCentre).toBeCloseTo(buttonCentre, 0);
			}
			expect(m.icon!.x).toBeCloseTo(m.label!.x, 0);
			expect(m.textBox!.x).toBeCloseTo(m.label!.x, 0);
			expect(m.box!.x).toBeCloseTo(m.label!.x, 0);
			expect(m.textBox!.right).toBeCloseTo(m.col!.right - (m.label!.x - m.col!.x - (width >= 768 ? 1 : 0)), 0);
		});

		test('history: section dividers run edge to edge of the column', async ({ page }) => {
			await openHistoryDetails(page);
			const m = await measure(page);
			const inner = width >= 768 ? { x: m.col!.x + 1, width: m.col!.width - 1 } : { x: m.col!.x, width: m.col!.width };
			for (const section of m.sections) {
				expect(section.rect!.x, section.id ?? '').toBeCloseTo(inner.x, 0);
				expect(section.rect!.width, section.id ?? '').toBeCloseTo(inner.width, 0);
			}
		});

		test('library: strip, gutter and full-width sections', async ({ page }) => {
			await openLibraryDetails(page);
			const m = await measure(page);
			expect(m.strip!.y).toBeCloseTo(m.col!.y + (width >= 768 ? 0 : 1), 0);
			if (width >= 768) {
				expect(m.strip!.bottom).toBeCloseTo(m.bar!.bottom, 0);
				expect(m.strip!.height).toBeCloseTo(m.bar!.height, 0);
			}
			expect(m.icon!.x).toBeCloseTo(m.box!.x, 0);
			const inner = width >= 768 ? { x: m.col!.x + 1, width: m.col!.width - 1 } : { x: m.col!.x, width: m.col!.width };
			for (const section of m.sections) expect(section.rect!.width, section.id ?? '').toBeCloseTo(inner.width, 0);
		});
	});
}
