import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner } from './helpers';

const NOW = '2026-11-12T08:00:00Z';
const SHOTS = process.env.KIT_PREVIEW_SHOTS ?? '';

interface Look {
	url: string;
	width: number;
	height: number;
}

interface WireItem {
	id: string;
	kit_id: string;
	name: string;
	kind: 'image' | 'video' | 'audio' | 'text';
	position: number;
	text: string | null;
	media: Record<string, unknown> | null;
	missing: boolean;
	created_at: string;
}

interface WireKit {
	id: string;
	name: string;
	handle: string;
	label: string | null;
	items: WireItem[];
}

function media(id: string, look?: Look) {
	const url = look?.url ?? 'data:image/gif;base64,R0lGODlhAQABAAAAACw=';
	return {
		upload_id: id,
		filename: `${id}.png`,
		original_filename: `${id}.png`,
		media_type: 'image',
		url,
		thumbnail_small: url,
		thumbnail_medium: url,
		width: look?.width ?? 1,
		height: look?.height ?? 1,
		duration_seconds: null,
		size: 70
	};
}

function figure(width: number, height: number, color: string): Look {
	const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><rect width="100%" height="100%" fill="${color}"/><rect x="4" y="4" width="${width - 8}" height="${height - 8}" fill="none" stroke="white" stroke-width="6"/><circle cx="${width / 2}" cy="${height * 0.16}" r="${Math.min(width, height) * 0.1}" fill="white"/><rect x="${width * 0.42}" y="${height * 0.3}" width="${width * 0.16}" height="${height * 0.55}" fill="white"/></svg>`;
	return { url: `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`, width, height };
}

function seed(): WireKit[] {
	const item = (kit: string, n: number, kind: WireItem['kind'], text: string | null, look: Look | null): WireItem => ({
		id: `it_${kit}${n}`,
		kit_id: kit,
		name: `${kind}${n}`,
		kind,
		position: n,
		text,
		media: kind === 'text' ? null : media(`${kit}${n}`, look ?? undefined),
		missing: false,
		created_at: NOW
	});
	const kit = (id: string, name: string, items: WireItem[]): WireKit => ({ id, name, handle: name.toLowerCase().replace(/\s+/g, '_'), label: null, items });
	return [
		kit('ka', 'Hero', [
			item('ka', 1, 'image', null, figure(400, 900, '#3b5b8c')),
			item('ka', 2, 'image', null, figure(900, 500, '#8c3b5b')),
			item('ka', 3, 'image', null, figure(600, 600, '#3b8c5b')),
			item('ka', 4, 'text', 'Warm', null)
		]),
		kit('kb', 'Harbour', [item('kb', 1, 'image', null, figure(1600, 600, '#8c6a3b')), item('kb', 2, 'image', null, figure(1200, 700, '#5b3b8c'))]),
		kit('kc', 'Portrait', [item('kc', 1, 'image', null, figure(500, 900, '#3b8c8c'))]),
		kit('kd', 'Quad', [
			item('kd', 1, 'image', null, figure(600, 900, '#8c3b3b')),
			item('kd', 2, 'image', null, figure(900, 600, '#3b3b8c')),
			item('kd', 3, 'image', null, figure(700, 700, '#6a8c3b')),
			item('kd', 4, 'image', null, figure(500, 900, '#8c3b8c'))
		]),
		kit('ke', 'Voice notes', [item('ke', 1, 'audio', null, null), item('ke', 2, 'text', 'Calm', null), item('ke', 3, 'text', 'Warm', null)])
	];
}

async function json(route: Route, data: unknown) {
	await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

async function mockKits(page: Page, kits: WireKit[]) {
	const counts = (kit: WireKit) => {
		const result = { image: 0, video: 0, audio: 0, text: 0 };
		for (const item of kit.items) result[item.kind] += 1;
		return result;
	};
	await page.route(/\/api\/kits(\/.*)?(\?.*)?$/, (route) => {
		const path = new URL(route.request().url()).pathname.replace(/^\/api\/kits\/?/, '');
		if (path !== '' || route.request().method() !== 'GET') return route.fallback();
		const rows = kits.map(({ items, ...rest }) => ({
			...rest,
			position: 0,
			counts: counts({ ...rest, items }),
			item_count: items.length,
			created_at: NOW,
			updated_at: NOW,
			thumbnails: items.filter((entry) => entry.media).slice(0, 4).map((entry) => ({
				item_id: entry.id,
				kind: entry.kind,
				url: entry.media!.url,
				thumbnail_small: entry.media!.thumbnail_small,
				thumbnail_medium: entry.media!.thumbnail_medium,
				width: entry.media!.width,
				height: entry.media!.height
			}))
		}));
		return json(route, { kits: rows, total: rows.length, labels: [] });
	});
	await page.route(/\/api\/library\/items(\?.*)?$/, (route) => json(route, { items: [], total: 0, limit: 24, offset: 0 }));
	await page.route(/\/api\/collections\/?(\?.*)?$/, (route) => json(route, { collections: [], smart_counts: { all: 0, favorites: 0, unsorted: 0 } }));
	await page.route(/\/api\/library\/facets$/, (route) => json(route, { media_types: {} }));
	await page.route(/\/api\/tags(\?.*)?$/, (route) => json(route, { tags: [] }));
}

test.describe('Kit card previews', () => {
	for (const size of [
		{ width: 1440, height: 900, name: 'kits-1440' },
		{ width: 390, height: 844, name: 'kits-390' }
	]) {
		test(`show every picture whole at its own aspect at ${size.width}px`, async ({ page }) => {
			await page.setViewportSize({ width: size.width, height: size.height });
			await loginAsOwner(page);
			await mockKits(page, seed());
			await page.goto('/library?view=kits');
			await expect(page.locator('[data-kit-card]')).toHaveCount(5);
			await expect(page.locator('[data-kit-preview-empty]')).toHaveCount(1);
			await page.waitForFunction(() => {
				const imgs = Array.from(document.querySelectorAll('[data-kit-preview] img')) as HTMLImageElement[];
				return imgs.length === 10 && imgs.every((img) => img.complete && img.naturalWidth > 0);
			});
			const boxes = await page.locator('[data-kit-preview] img').evaluateAll((imgs) =>
				imgs.map((img) => {
					const el = img as HTMLImageElement;
					const rect = el.getBoundingClientRect();
					return { box: rect.width / rect.height, natural: el.naturalWidth / el.naturalHeight };
				})
			);
			for (const entry of boxes) expect(Math.abs(entry.box - entry.natural) / entry.natural).toBeLessThan(0.03);
			if (SHOTS) await page.screenshot({ path: `${SHOTS}/${size.name}.png`, fullPage: true });
		});
	}
});
