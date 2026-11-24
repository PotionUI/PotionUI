import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner } from './helpers';

const NOW = '2026-11-12T08:00:00Z';
const TOTAL = 60;

async function json(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

function wireKit(index: number) {
	return {
		id: `kt_${index.toString(16).padStart(8, '0')}`,
		name: `Kit ${index}`,
		handle: `kit_${index}`,
		label: null,
		position: index,
		counts: { image: 0, video: 0, audio: 0, text: 0 },
		item_count: 0,
		created_at: NOW,
		updated_at: NOW,
		thumbnails: []
	};
}

async function mockBackend(page: Page) {
	const queries: URLSearchParams[] = [];
	await page.route(/\/api\/kits(\/.*)?(\?.*)?$/, async (route) => {
		const url = new URL(route.request().url());
		const path = url.pathname.replace(/^\/api\/kits\/?/, '');
		if (path !== '') {
			return route.fulfill({
				status: 404,
				contentType: 'application/json',
				body: JSON.stringify({ detail: { success: false, error: 'kit_not_found', message: 'Kit not found' } })
			});
		}
		if (url.searchParams.get('limit') !== '1') queries.push(url.searchParams);
		const size = Number(url.searchParams.get('limit') ?? 24);
		const start = Number(url.searchParams.get('offset') ?? 0);
		const kits = Array.from({ length: Math.max(0, Math.min(size, TOTAL - start)) }, (_, offset) => wireKit(start + offset + 1));
		return json(route, { kits, total: TOTAL, labels: [] });
	});
	await page.route(/\/api\/library\/items(\?.*)?$/, (route) => json(route, { items: [], total: 0, limit: 24, offset: 0 }));
	await page.route(/\/api\/collections\/?(\?.*)?$/, (route) => json(route, { collections: [], smart_counts: { all: 0, favorites: 0, unsorted: 0 } }));
	await page.route(/\/api\/library\/facets$/, (route) => json(route, { media_types: {} }));
	await page.route(/\/api\/tags(\?.*)?$/, (route) => json(route, { tags: [] }));
	return queries;
}

test.describe('Kits list state survives opening a kit', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await loginAsOwner(page);
	});

	test('a plain ?view=kits URL still gives page one in manual order', async ({ page }) => {
		const queries = await mockBackend(page);
		await page.goto('/library?view=kits');
		await expect(page.locator('[data-kit-card]').first()).toBeVisible();
		expect(queries[0].get('offset')).toBeNull();
		expect(queries[0].get('sort_by')).toBeNull();
	});

	test('page and sort come back after opening a kit and going back', async ({ page }) => {
		const queries = await mockBackend(page);
		await page.goto('/library?view=kits&page=2&sort=name&dir=desc');
		await expect(page.locator('[data-kit-card="kt_00000019"]')).toBeVisible();
		expect(queries[0].get('offset')).toBe('24');
		expect(queries[0].get('sort_by')).toBe('name');
		expect(queries[0].get('sort_dir')).toBe('desc');

		await page.getByRole('button', { name: 'Open Kit 25' }).click();
		await expect(page).toHaveURL(/kit=kt_00000019/);
		await expect(page).toHaveURL(/page=2/);

		await page.goBack();
		await expect(page).not.toHaveURL(/kit=/);
		await expect(page).toHaveURL(/page=2/);
		await expect(page).toHaveURL(/sort=name/);
		await expect(page.locator('[data-kit-card="kt_00000019"]')).toBeVisible();
		const last = queries[queries.length - 1];
		expect(last.get('offset')).toBe('24');
		expect(last.get('sort_by')).toBe('name');
	});

	test('changing the page writes it to the URL', async ({ page }) => {
		await mockBackend(page);
		await page.goto('/library?view=kits');
		await page.getByRole('navigation', { name: 'Pagination' }).getByRole('button', { name: 'Page 2' }).click();
		await expect(page).toHaveURL(/page=2/);
	});
});
