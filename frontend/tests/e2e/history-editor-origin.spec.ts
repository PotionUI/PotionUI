import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { mockMediaTools, solidPng } from './mediaToolMocks';

const JOURNEY = 'history-editor-origin';
const PICTURE = solidPng(640, 480);
const EDIT_ID = 'ed_e2e_origin';
const GONE_ID = 'ed_e2e_gone';

function file(id: number) {
	return { id, file_path: `e2e/editor-origin-${id}.png`, file_type: 'image', is_final: true, created_at: new Date().toISOString(), width: 1024, height: 768 };
}

function item(id: string, minutesAgo: number, over: Record<string, unknown> = {}) {
	const created = new Date(Date.now() - minutesAgo * 60_000).toISOString();
	return {
		id,
		preset_id: 'qwen21',
		preset_name: 'Qwen-Image 2.1',
		mode: 'edit',
		form_data: { prompt: id },
		status: 'completed',
		progress: 1,
		created_at: created,
		started_at: created,
		completed_at: created,
		updated_at: created,
		run_seconds: null,
		queued_seconds: null,
		rating: 0,
		is_favorite: false,
		tags: [],
		files: [file(Number(id.slice(-1)) * 10)],
		origin: null,
		...over
	};
}

const MADE = item('01EDITORORIGIN0000000001', 2, { origin: { kind: 'editor', edit_id: EDIT_ID, step_id: 's7', task: 'edit_region', run_id: 'er_1' } });
const DELETED = item('01EDITORORIGIN0000000002', 9, { origin: { kind: 'editor', edit_id: GONE_ID, step_id: 's1', task: 'edit_region', run_id: 'er_2' } });
const PLAIN = item('01EDITORORIGIN0000000003', 30);
const ITEMS = [MADE, DELETED, PLAIN];

async function mockHistory(page: Page) {
	await mockMediaTools(page);
	await page.route('**/api/generations/history**', async (route) => {
		const path = new URL(route.request().url()).pathname;
		if (path === '/api/generations/history') return route.fulfill({ json: { success: true, data: { generations: ITEMS, total: ITEMS.length } } });
		const found = ITEMS.find((entry) => path === `/api/generations/history/${entry.id}`);
		if (found) return route.fulfill({ json: { success: true, data: found } });
		return route.fallback();
	});
	await page.route(/\/api\/edits\/[^/?]+(\?.*)?$/, async (route) => {
		const id = new URL(route.request().url()).pathname.split('/').pop() as string;
		if (route.request().method() !== 'GET') return route.fallback();
		if (id === GONE_ID) return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: { error: 'edit_not_found', message: 'This edit does not exist' } }) });
		const now = new Date().toISOString();
		return route.fulfill({
			json: { success: true, data: { edit: { id, tool: 'image', name: null, source: { kind: 'history', id: '10' }, thumbnail_upload_id: null, revision: 1, created_at: now, updated_at: now, state: {} } } }
		});
	});
	await page.route(/\/api\/edits(\?.*)?$/, (route) => (route.request().method() === 'GET' ? route.fulfill({ json: { success: true, data: { edits: [] } } }) : route.fallback()));
	await page.route('**/*', async (route) => {
		const request = route.request();
		if (request.resourceType() === 'image' && /editor-origin-|\/api\/media\/files\//.test(request.url())) return route.fulfill({ body: PICTURE, contentType: 'image/png' });
		return route.fallback();
	});
}

async function openDetails(page: Page, index: number) {
	const actions = page.locator('[data-generation-card]').getByRole('button', { name: 'Item actions' });
	await actions.nth(index).click({ force: true });
	await page.getByRole('menuitem', { name: 'Open details' }).click();
	await expect(page.getByRole('dialog', { name: 'Generation Details' })).toBeVisible({ timeout: 15000 });
}

for (const viewport of [
	{ width: 1440, height: 900 },
	{ width: 390, height: 844 }
]) {
	test(`history marks what was made in the editor and opens the edit at ${viewport.width}`, async ({ page }) => {
		await page.setViewportSize(viewport);
		await mockHistory(page);
		await loginAsOwner(page);
		await page.goto('/history');
		await page.waitForLoadState('networkidle');

		const cards = page.locator('[data-generation-card]');
		await expect(cards).toHaveCount(ITEMS.length, { timeout: 15000 });
		await expect(page.locator('[data-editor-origin]')).toHaveCount(2);
		await expect(cards.nth(2).locator('[data-editor-origin]')).toHaveCount(0);
		await expect(cards.nth(0).locator('[data-editor-origin="open"]')).toHaveAttribute('aria-label', 'Made in the editor');
		await screenshot(page, JOURNEY, `12-history-card-dark-${viewport.width}`);

		await cards.nth(1).locator('[data-editor-origin="open"]').click({ force: true });
		await expect(cards.nth(1).locator('[data-editor-origin="deleted"]')).toBeAttached();
		await expect(page.getByText('That edit no longer exists.')).toBeVisible();
		await expect(page).toHaveURL(/\/history/);

		await openDetails(page, 0);
		const origin = page.locator('[data-details-origin]');
		await expect(origin.locator('[data-editor-origin="open"]')).toContainText('Made in the editor');
		if (viewport.width === 1440) await screenshot(page, JOURNEY, `12b-history-details-dark-${viewport.width}`);
		await origin.locator('[data-editor-origin="open"]').click();
		await expect(page).toHaveURL(/\/generate/, { timeout: 15000 });
		await expect(page.getByRole('dialog', { name: 'Generation Details' })).toHaveCount(0);
		await expect(page.locator('[data-image-editor], [data-editor-frame]').first()).toBeVisible({ timeout: 15000 });
	});
}
