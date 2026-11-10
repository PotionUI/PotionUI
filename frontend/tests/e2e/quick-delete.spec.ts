import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { fixturePng } from './imageEditorHelpers';

const JOURNEY = 'quick-delete';
const PNG = fixturePng(96);
const PNG_URL = `data:image/png;base64,${PNG.toString('base64')}`;
const COLLECTION = { id: 'col-blue', name: 'Blue glass' };

function generation(id: string, index: number) {
	const created = `2026-01-0${index}T00:00:00Z`;
	return {
		id,
		preset_id: 'preset-a',
		preset_name: 'Landscape',
		status: 'completed',
		progress: 1,
		created_at: created,
		updated_at: created,
		form_data: { prompt: `prompt for ${id}` },
		files: [
			{
				id: index,
				file_path: `out/${id}/image.png`,
				file_type: 'image',
				is_final: true,
				created_at: created,
				width: 96,
				height: 96,
				thumbnail_small: PNG_URL,
				thumbnail_medium: PNG_URL,
				thumbnail_large: PNG_URL
			}
		],
		rating: 0,
		is_favorite: false,
		tags: []
	};
}

function libraryRow(id: string, name: string) {
	return {
		id,
		filename: `${id}-file.png`,
		original_filename: name,
		media_type: 'image',
		url: PNG_URL,
		thumbnail_medium: PNG_URL,
		width: 96,
		height: 96,
		created_at: '2026-01-01T00:00:00Z',
		tags: [],
		is_favorite: false
	};
}

async function json(route: Route, data: unknown) {
	await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

interface Backend {
	generations: ReturnType<typeof generation>[];
	members: Set<string>;
	rows: ReturnType<typeof libraryRow>[];
	singleDeletes: string[];
	bulkDeletes: string[][];
	libraryDeletes: string[];
	entryDeletes: string[];
}

async function mockBackend(page: Page): Promise<Backend> {
	const backend: Backend = {
		generations: [generation('gen-1', 3), generation('gen-2', 2), generation('gen-3', 1)],
		members: new Set(['gen-2', 'gen-3']),
		rows: [libraryRow('lib-1', 'harbour.png'), libraryRow('lib-2', 'dunes.png'), libraryRow('lib-3', 'forest.png')],
		singleDeletes: [],
		bulkDeletes: [],
		libraryDeletes: [],
		entryDeletes: []
	};
	const drop = (ids: string[]) => {
		backend.generations = backend.generations.filter((g) => !ids.includes(g.id));
		for (const id of ids) backend.members.delete(id);
	};

	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) => {
		const url = new URL(route.request().url());
		const inCollection = url.searchParams.get('collection_id') === COLLECTION.id;
		const generations = inCollection ? backend.generations.filter((g) => backend.members.has(g.id)) : backend.generations;
		return json(route, { generations, total: generations.length });
	});
	await page.route(/\/api\/generations\/history\/bulk-delete$/, async (route) => {
		const ids = (route.request().postDataJSON() as { generation_ids: string[] }).generation_ids;
		backend.bulkDeletes.push(ids);
		drop(ids);
		await json(route, { message: 'ok', deleted_count: ids.length, failed_count: 0 });
	});
	await page.route(/\/api\/generations\/history\/gen-\d+$/, async (route) => {
		if (route.request().method() !== 'DELETE') return route.fallback();
		const id = route.request().url().split('/').pop()!;
		backend.singleDeletes.push(id);
		drop([id]);
		await json(route, { message: 'ok' });
	});
	await page.route(/\/api\/collections\/?(\?.*)?$/, (route) => {
		const scope = new URL(route.request().url()).searchParams.get('scope');
		const count = scope === 'history' ? backend.members.size : 0;
		const collections = scope === 'history' ? [{ ...COLLECTION, parent_id: null, scope, item_count: count }] : [];
		return json(route, { collections, total: collections.length, smart_counts: { all: backend.generations.length, favorites: 0, unsorted: 1 } });
	});
	await page.route(new RegExp(`/api/collections/${COLLECTION.id}/delete-entries$`), async (route) => {
		backend.entryDeletes.push(COLLECTION.id);
		const total = backend.members.size;
		drop([...backend.members]);
		await json(route, { deleted: total, blocked: [], total });
	});
	await page.route(/\/api\/library\/items(\?.*)?$/, (route) =>
		json(route, { items: backend.rows, total: backend.rows.length, limit: 24, offset: 0 })
	);
	await page.route(/\/api\/library\/items\/lib-\d+$/, async (route) => {
		if (route.request().method() !== 'DELETE') return route.fallback();
		const id = route.request().url().split('/').pop()!;
		backend.libraryDeletes.push(id);
		backend.rows = backend.rows.filter((row) => row.id !== id);
		await json(route, { id, deleted: true });
	});
	await page.route(/\/api\/tags(\?.*)?$/, (route) => json(route, { tags: [] }));
	await page.route(/\/api\/keybindings$/, async (route) => {
		const response = await route.fetch();
		const body = await response.json();
		const list = body.data?.keybindings ?? body.data ?? [];
		if (!list.some((b: { action_id: string }) => b.action_id === 'delete_current_generation')) {
			list.push({
				action_id: 'delete_current_generation',
				key: 'Delete',
				modifiers: '',
				label: 'Delete Generation',
				category: 'generation',
				context: 'generate',
				description: 'Delete the last generation of the selected tab',
				enabled: true,
				is_custom: false
			});
		}
		await json(route, { keybindings: list });
	});
	return backend;
}

const dialog = (page: Page) => page.getByRole('alertdialog');
const confirmButton = (page: Page) => dialog(page).getByRole('button', { name: /^(Delete|Confirm)/ });

async function leaveFocus(page: Page) {
	await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
}

async function selectCard(page: Page, card: ReturnType<Page['locator']>, name: string) {
	await card.hover();
	await card.getByRole('button', { name }).click();
	await leaveFocus(page);
}

for (const viewport of [
	{ width: 1440, height: 900 },
	{ width: 390, height: 844 }
]) {
	test.describe(`quick delete at ${viewport.width}`, () => {
		test.beforeEach(async ({ page }) => {
			await page.setViewportSize(viewport);
			await loginAsOwner(page);
		});

		test('Delete with one generation selected on History opens the single delete dialog', async ({ page }) => {
			const backend = await mockBackend(page);
			await page.goto('/history');
			const cards = page.locator('[data-generation-card]');
			await expect(cards).toHaveCount(3);

			await page.keyboard.press('Delete');
			await expect(dialog(page)).toHaveCount(0);

			await selectCard(page, page.locator('[data-generation-card][data-generation-id="gen-1"]'), 'Select generation');
			await page.keyboard.press('Delete');
			await expect(dialog(page)).toContainText('Delete Generation');
			await expect(dialog(page)).toContainText('1 generated file(s)');
			await screenshot(page, JOURNEY, `history-single-${viewport.width}`);

			await confirmButton(page).click();
			await expect(cards).toHaveCount(2);
			expect(backend.singleDeletes).toEqual(['gen-1']);
		});

		test('Delete with several generations selected on History opens the bulk dialog', async ({ page }) => {
			const backend = await mockBackend(page);
			await page.goto('/history');
			const cards = page.locator('[data-generation-card]');
			await expect(cards).toHaveCount(3);
			await selectCard(page, page.locator('[data-generation-card][data-generation-id="gen-1"]'), 'Select generation');
			await selectCard(page, page.locator('[data-generation-card][data-generation-id="gen-2"]'), 'Select generation');

			await page.keyboard.press('Delete');
			await expect(dialog(page)).toContainText('Delete Multiple Generations');
			await expect(dialog(page)).toContainText('delete 2 generation(s)');
			await screenshot(page, JOURNEY, `history-bulk-${viewport.width}`);

			await page.keyboard.press('Delete');
			await expect(dialog(page)).toHaveCount(1);
			await confirmButton(page).click();
			await expect(cards).toHaveCount(1);
			expect(backend.bulkDeletes).toEqual([['gen-1', 'gen-2']]);
		});

		test('Delete on Library opens the single dialog for one item and the bulk dialog for several', async ({ page }) => {
			const backend = await mockBackend(page);
			await page.goto('/library');
			const card = (name: string) => page.locator('[data-library-card]').filter({ hasText: name });
			await expect(page.locator('[data-library-card]')).toHaveCount(3);

			await selectCard(page, card('harbour.png'), 'Select item');
			await page.keyboard.press('Delete');
			await expect(dialog(page)).toContainText('Delete library item');
			await expect(dialog(page)).toContainText('harbour.png');
			await screenshot(page, JOURNEY, `library-single-${viewport.width}`);
			await dialog(page).getByRole('button', { name: 'Cancel' }).click();
			await expect(dialog(page)).toHaveCount(0);
			expect(backend.libraryDeletes).toEqual([]);

			await selectCard(page, card('dunes.png'), 'Select item');
			await page.keyboard.press('Delete');
			await expect(dialog(page)).toContainText('Delete library items');
			await expect(dialog(page)).toContainText('Delete 2 item(s)');
			await screenshot(page, JOURNEY, `library-bulk-${viewport.width}`);
			await confirmButton(page).click();
			await expect(page.locator('[data-library-card]')).toHaveCount(1);
			expect(backend.libraryDeletes.sort()).toEqual(['lib-1', 'lib-2']);
		});

		test('Delete entries empties a History collection and keeps the collection', async ({ page }) => {
			const backend = await mockBackend(page);
			await page.goto('/history');
			await expect(page.locator('[data-generation-card]')).toHaveCount(3);
			if (viewport.width < 768) await page.getByRole('button', { name: 'Show collections' }).click();

			await page.getByRole('treeitem', { name: new RegExp(COLLECTION.name) }).hover();
			await page.getByRole('button', { name: `${COLLECTION.name} actions` }).click();
			await page.getByRole('menuitem', { name: 'Delete entries…' }).click();
			await expect(dialog(page)).toContainText('Delete entries?');
			await expect(dialog(page)).toContainText('2 generations');
			await expect(dialog(page)).toContainText('not only removed from this collection');
			await expect(dialog(page)).toContainText('The collection itself stays');
			await expect(dialog(page)).toContainText('cannot be undone');
			await screenshot(page, JOURNEY, `collection-entries-${viewport.width}`);

			await confirmButton(page).click();
			await expect(page.getByText('Deleted 2 generations.')).toBeVisible();
			expect(backend.entryDeletes).toEqual([COLLECTION.id]);
			await expect(page.locator('[data-generation-card]')).toHaveCount(1);
			await expect(page.getByRole('treeitem', { name: new RegExp(COLLECTION.name) })).toHaveCount(1);
		});

		test('the Delete shortcut is listed with the generate page shortcuts', async ({ page }) => {
			await mockBackend(page);
			const bindingsLoaded = page.waitForResponse(/\/api\/keybindings$/);
			await page.goto('/generate');
			await bindingsLoaded;
			const shortcuts = page.getByRole('dialog', { name: 'Keyboard Shortcuts' });
			const deleteKey = shortcuts.locator('kbd', { hasText: /^Delete$/ });
			const sidebarButton = page.getByRole('button', { name: 'Keyboard shortcuts', exact: true });

			const desktop = viewport.width >= 768;
			if (desktop) {
				await sidebarButton.click();
				await expect(shortcuts).toBeVisible();
				await shortcuts.getByPlaceholder('Filter shortcuts...').fill('delete');
				await expect(deleteKey).toBeVisible();
				await screenshot(page, JOURNEY, `shortcuts-${viewport.width}`);
				await page.keyboard.press('Escape');
				await expect(shortcuts).toHaveCount(0);
			}

			await leaveFocus(page);
			await expect.poll(() => page.evaluate(() => document.activeElement === document.body)).toBe(true);
			await expect(async () => {
				if (!(await shortcuts.isVisible())) await page.keyboard.press('?');
				await expect(shortcuts).toBeVisible({ timeout: 500 });
			}).toPass({ timeout: 10000 });
			await shortcuts.getByPlaceholder('Filter shortcuts...').fill('delete');
			await expect(deleteKey).toBeVisible();
			if (!desktop) await screenshot(page, JOURNEY, `shortcuts-${viewport.width}`);
		});
	});
}
