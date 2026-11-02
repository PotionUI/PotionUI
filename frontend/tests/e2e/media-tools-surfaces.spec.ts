import { test, expect, type Locator, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot, shotPath } from './helpers';
import { fixturePng } from './imageEditorHelpers';

const JOURNEY = 'media-tools-surfaces';

const PNG = fixturePng(96);
const PNG_URL = `data:image/png;base64,${PNG.toString('base64')}`;

function tool(id: string, label: string, extra: Record<string, unknown> = {}) {
	return {
		id,
		label,
		icon: 'sliders',
		accepts: ['image'],
		produces: 'same',
		cardinality: 'one_to_one',
		params: [],
		requires: {},
		selection: null,
		scopes: ['library', 'history', 'field'],
		editor: null,
		preview: false,
		component: null,
		runnable: true,
		source: 'core',
		last_params: null,
		...extra
	};
}

const CATALOG = [
	tool('trim', 'Trim', { icon: 'scissors', accepts: ['video', 'audio'], editor: 'trim' }),
	tool('crop', 'Crop', { icon: 'crop', accepts: ['image', 'video'], editor: 'crop' }),
	tool('resize', 'Resize', { icon: 'shrink', accepts: ['image', 'video'], editor: 'resize' }),
	tool('rotate', 'Rotate', {
		accepts: ['image', 'video'],
		preview: true,
		params: [{ name: 'degrees', type: 'select', label: 'Degrees', default: '90', options: ['90', '180', '270'] }]
	}),
	tool('flip', 'Flip', { accepts: ['image', 'video'] }),
	tool('speed', 'Speed', { icon: 'gauge', accepts: ['video', 'audio'] }),
	tool('fit_canvas', 'Fit canvas', { accepts: ['image'] }),
	tool('color_key', 'Color key', { accepts: ['image'] }),
	tool('crop_subject', 'Crop to subject', { accepts: ['image'], requires: { has_alpha: true } }),
	tool('frame', 'Frame', { icon: 'photo', accepts: ['video'], produces: 'image', editor: 'frame' }),
	tool('split', 'Split', { icon: 'split', accepts: ['audio'], cardinality: 'one_to_many', editor: 'split' }),
	tool('concat', 'Concat', { accepts: ['video'], cardinality: 'many_to_one', selection: { min: 2, max: 8 } })
];

function libraryRow(id: string, name: string, kind: 'image' | 'video' | 'audio') {
	return {
		id,
		filename: `${id}-file.${kind === 'image' ? 'png' : kind === 'video' ? 'mp4' : 'wav'}`,
		original_filename: name,
		media_type: kind,
		url: PNG_URL,
		thumbnail_medium: kind === 'audio' ? undefined : PNG_URL,
		width: 96,
		height: 96,
		duration_seconds: kind === 'image' ? undefined : 8,
		created_at: '2026-01-01T00:00:00Z',
		tags: [],
		is_favorite: false,
		has_alpha: kind === 'image' ? false : undefined
	};
}

function generation(id: string, kind: 'image' | 'video', index: number) {
	return {
		id,
		preset_id: 'preset-a',
		preset_name: 'Landscape',
		status: 'completed',
		progress: 1,
		created_at: `2026-01-0${3 - index}T00:00:00Z`,
		updated_at: `2026-01-0${3 - index}T00:00:00Z`,
		form_data: { prompt: `prompt for ${id}` },
		files: [
			{
				id: index,
				file_path: `out/${id}/clip.${kind === 'image' ? 'png' : 'mp4'}`,
				file_type: kind,
				is_final: true,
				created_at: '2026-01-01T00:00:00Z',
				width: 96,
				height: 96,
				duration_seconds: kind === 'video' ? 8 : undefined,
				thumbnail_small: PNG_URL,
				thumbnail_medium: PNG_URL,
				thumbnail_large: PNG_URL
			}
		],
		rating: 0,
		is_favorite: false
	};
}

async function json(route: Route, data: unknown) {
	await route.fulfill({
		status: 200,
		contentType: 'application/json',
		body: JSON.stringify({ success: true, data })
	});
}

interface Backend {
	library: ReturnType<typeof libraryRow>[];
	runs: { started: unknown[]; cancelled: string[]; progress: number; finish: boolean; fail?: boolean };
}

async function mockBackend(page: Page): Promise<Backend> {
	const backend: Backend = {
		library: [
			libraryRow('lib-img', 'harbour.png', 'image'),
			libraryRow('lib-vid', 'timelapse.mp4', 'video'),
			libraryRow('lib-aud', 'ambience.wav', 'audio')
		],
		runs: { started: [], cancelled: [], progress: 0.42, finish: false }
	};
	const generations = [generation('gen-1', 'image', 1), generation('gen-2', 'video', 2)];

	await page.route(/\/api\/media-tools(\?.*)?$/, (route) => json(route, { tools: CATALOG }));
	await page.route(/\/api\/media-tools\/runs$/, async (route) => {
		backend.runs.started.push(route.request().postDataJSON());
		await json(route, { run_id: 'run-1' });
	});
	await page.route(/\/api\/media-tools\/runs\/run-1$/, async (route) => {
		if (route.request().method() === 'DELETE') {
			backend.runs.cancelled.push('run-1');
			return json(route, {
				run_id: 'run-1',
				status: 'cancelled',
				progress: backend.runs.progress,
				label: '',
				items: [],
				replaced: false,
				error: null
			});
		}
		if (backend.runs.fail) {
			return json(route, {
				run_id: 'run-1',
				status: 'failed',
				progress: 0,
				label: '',
				items: [],
				replaced: false,
				error: { code: 'x', message: 'The encoder stopped' }
			});
		}
		if (backend.runs.finish) {
			return json(route, {
				run_id: 'run-1',
				status: 'completed',
				progress: 1,
				label: '',
				replaced: false,
				error: null,
				items: [
					{
						id: 'lib-new',
						filename: 'lib-new-file.png',
						original_filename: 'harbour-rotated.png',
						media_type: 'image',
						url: PNG_URL,
						width: 96,
						height: 96
					}
				]
			});
		}
		return json(route, {
			run_id: 'run-1',
			status: 'running',
			progress: backend.runs.progress,
			label: 'Rotating',
			items: [],
			replaced: false,
			error: null
		});
	});
	await page.route(/\/api\/media-tools\/preview$/, (route) =>
		route.fulfill({ status: 200, contentType: 'image/webp', body: PNG })
	);
	await page.route(/\/api\/library\/items\/[^/?]+$/, async (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		const id = decodeURIComponent(route.request().url().split('/').pop() ?? '');
		const found = id === 'lib-new' ? libraryRowAfter() : backend.library.find((entry) => entry.id === id);
		if (!found) {
			return route.fulfill({ status: 404, contentType: 'application/json', body: '{"success":false}' });
		}
		return json(route, found);
	});
	await page.route(/\/api\/library\/items(\?.*)?$/, (route) =>
		json(route, { items: backend.library, total: backend.library.length, limit: 24, offset: 0 })
	);
	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) =>
		json(route, { generations, total: generations.length })
	);
	await page.route(/\/api\/generations\/history\/gen-\d+(\?.*)?$/, (route) => {
		const id = route.request().url().match(/gen-\d+/)![0];
		return json(route, generations.find((entry) => entry.id === id));
	});
	await page.route(/\/api\/tags(\?.*)?$/, (route) => json(route, { tags: [] }));
	await page.route(/\/api\/media\/generations\/gen-\d+\/.+$/, (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PNG })
	);
	return backend;
}

const menu = (page: Page) => page.locator('[data-tools-menu]');

type Submenu = 'edit' | 'effects';

const SUBMENU_LABEL: Record<Submenu, string> = { edit: 'Edit', effects: 'Effects' };

async function topRows(page: Page): Promise<Array<{ id: string; count: number }>> {
	return page.locator('[data-tools-menu] [data-submenu-row]').evaluateAll((rows) =>
		rows.map((row) => ({
			id: row.getAttribute('data-submenu-row') ?? '',
			count: Number(row.querySelector('[data-submenu-count]')?.textContent ?? '0')
		}))
	);
}

async function groupLabels(page: Page): Promise<string[]> {
	const rows = (await topRows(page)).map((row) => SUBMENU_LABEL[row.id as Submenu]);
	const groups = await page
		.locator('[data-tools-menu] [role="group"]')
		.evaluateAll((nodes) => nodes.map((group) => group.getAttribute('aria-label') ?? ''));
	return [...rows, ...groups];
}

async function openSubmenu(page: Page, id: Submenu) {
	await menu(page).locator(`[data-submenu-row="${id}"]`).click();
	await expect(menu(page).locator('[data-submenu-back]')).toBeVisible();
}

async function closeSubmenu(page: Page, id: Submenu) {
	await menu(page).locator('[data-submenu-back]').click();
	await expect(menu(page).locator(`[data-submenu-row="${id}"]`)).toBeFocused();
}

async function toolsOf(page: Page, group: string): Promise<string[]> {
	const id = (Object.keys(SUBMENU_LABEL) as Submenu[]).find((key) => SUBMENU_LABEL[key] === group);
	if (!id) {
		return page
			.locator(`[data-tools-menu] [role="group"][aria-label="${group}"] [data-tool]`)
			.evaluateAll((rows) => rows.map((row) => row.getAttribute('data-tool') ?? ''));
	}
	await openSubmenu(page, id);
	const tools = await menu(page)
		.locator(`[data-submenu-group="${id}"] [data-tool]`)
		.evaluateAll((rows) => rows.map((row) => row.getAttribute('data-tool') ?? ''));
	await closeSubmenu(page, id);
	return tools;
}

async function openLibraryMenu(page: Page, name: string) {
	await page.goto('/library');
	await expect(page.locator('[data-library-card]')).toHaveCount(3);
	await page.locator('[data-library-card]').filter({ hasText: name }).getByRole('button', { name: 'Select item' }).click();
	await page.locator('[data-tools-trigger]').first().click();
	await expect(menu(page)).toBeVisible();
}

async function openHistoryMenu(page: Page, index: number) {
	await page.goto('/history');
	const selectors = page.getByRole('button', { name: 'Select generation' });
	await expect(selectors).toHaveCount(2);
	await selectors.nth(index).click();
	await page.locator('[data-tools-trigger]').first().click();
	await expect(menu(page)).toBeVisible();
}

test.describe('Tools menu, Edit and Effects', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await loginAsOwner(page);
		await mockBackend(page);
	});

	test('Library: an image gets Edit then Effects ahead of the existing groups', async ({ page }) => {
		await openLibraryMenu(page, 'harbour.png');

		const labels = await groupLabels(page);
		expect(labels.slice(0, 2)).toEqual(['Edit', 'Effects']);
		expect(labels).toEqual(expect.arrayContaining(['Analyze', 'Compose', 'Export']));
		expect(await topRows(page)).toEqual([
			{ id: 'edit', count: 7 },
			{ id: 'effects', count: 1 }
		]);
		await expect(menu(page).locator('[data-tool="crop"]')).toHaveCount(0);
		await screenshot(page, JOURNEY, 'library-image-menu-1440');
		expect(await toolsOf(page, 'Edit')).toEqual([
			'crop',
			'resize',
			'rotate',
			'flip',
			'fit_canvas',
			'color_key',
			'crop_subject'
		]);
		expect(await toolsOf(page, 'Effects')).toEqual(['apply-look']);
		await openSubmenu(page, 'edit');
		await expect(menu(page).locator('[data-tool="crop_subject"]')).toHaveAttribute('aria-disabled', 'true');
		await expect(menu(page).locator('[data-tool="crop_subject"] [data-tool-sub]')).toHaveText(
			'Needs a transparent image'
		);
		await screenshot(page, JOURNEY, 'library-image-edit-submenu-1440');
	});

	test('Library: a video gets the video tools and no Effects, with Concat waiting for a second clip', async ({ page }) => {
		await openLibraryMenu(page, 'timelapse.mp4');

		expect(await topRows(page)).toEqual([{ id: 'edit', count: 8 }]);
		await screenshot(page, JOURNEY, 'library-video-menu-1440');
		expect(await toolsOf(page, 'Edit')).toEqual(['trim', 'crop', 'resize', 'rotate', 'flip', 'speed', 'frame', 'concat']);
		expect(await groupLabels(page)).not.toContain('Effects');
		await openSubmenu(page, 'edit');
		await expect(menu(page).locator('[data-tool="concat"] [data-tool-sub]')).toHaveText('Select 2 to 8 items');
		await expect(menu(page).locator('[data-tool="frame"] [data-tool-sub]')).toHaveCount(0);
		await screenshot(page, JOURNEY, 'library-video-edit-submenu-1440');
	});

	test('Library: audio gets trim, speed and split and no Effects', async ({ page }) => {
		await openLibraryMenu(page, 'ambience.wav');

		expect(await toolsOf(page, 'Edit')).toEqual(['trim', 'speed', 'split']);
		expect(await groupLabels(page)).not.toContain('Effects');
		await screenshot(page, JOURNEY, 'library-audio-menu-1440');
	});

	test('History shows the same Edit and Effects groups as Library', async ({ page }) => {
		await openHistoryMenu(page, 0);
		const historyImage = { edit: await toolsOf(page, 'Edit'), effects: await toolsOf(page, 'Effects') };
		await page.keyboard.press('Escape');

		await openLibraryMenu(page, 'harbour.png');
		expect(historyImage.edit).toEqual(await toolsOf(page, 'Edit'));
		expect(historyImage.effects).toEqual(await toolsOf(page, 'Effects'));

		await openHistoryMenu(page, 1);
		expect(await toolsOf(page, 'Edit')).toEqual(['trim', 'crop', 'resize', 'rotate', 'flip', 'speed', 'frame', 'concat']);
		expect(await groupLabels(page)).not.toContain('Effects');
		await screenshot(page, JOURNEY, 'history-video-menu-1440');
	});

	test('Apply a look opens an Image editor tab on Effects', async ({ page }) => {
		await openLibraryMenu(page, 'harbour.png');

		await openSubmenu(page, 'effects');
		await expect(menu(page).locator('[data-tab-footer]')).toContainText('Image editor tab');
		await menu(page).locator('[data-tool="apply-look"]').click();

		await expect(page).toHaveURL(/\/generate/);
		await expect(page.getByTestId('tool-tab-host')).toHaveAttribute('data-tool', 'image');
		await screenshot(page, JOURNEY, 'apply-look-1440');
	});
});

test.describe('recent looks', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await loginAsOwner(page);
	});

	test('lists the looks applied last under Apply a look, on Library and History', async ({ page }) => {
		await page.addInitScript(() => {
			localStorage.setItem(
				'potionui-recent-looks',
				JSON.stringify([
					{ id: 'noir', name: 'Noir', group: 'Black and white' },
					{ id: 'ember', name: 'Ember', group: 'Colour' },
					{ id: 'honey', name: 'Honey', group: 'Colour' },
					{ id: 'frost', name: 'Frost', group: 'Colour' }
				])
			);
		});
		await mockBackend(page);

		await openLibraryMenu(page, 'harbour.png');
		expect(await toolsOf(page, 'Effects')).toEqual(['apply-look', 'look:noir', 'look:ember', 'look:honey']);
		await openSubmenu(page, 'effects');
		await expect(menu(page).locator('[data-tool="look:noir"] [data-tool-sub]')).toHaveText('Black and white');
		await screenshot(page, JOURNEY, 'recent-looks-submenu-1440');
		await page.keyboard.press('Escape');

		await openHistoryMenu(page, 0);
		expect(await toolsOf(page, 'Effects')).toEqual(['apply-look', 'look:noir', 'look:ember', 'look:honey']);
	});

	test('opens an Image editor tab for the look that was clicked', async ({ page }) => {
		await page.addInitScript(() => {
			localStorage.setItem(
				'potionui-recent-looks',
				JSON.stringify([{ id: 'ember', name: 'Ember', group: 'Colour' }])
			);
		});
		await mockBackend(page);
		await openLibraryMenu(page, 'harbour.png');

		await openSubmenu(page, 'effects');
		await menu(page).locator('[data-tool="look:ember"]').click();

		await expect(page).toHaveURL(/\/generate/);
		await expect(page.getByTestId('tool-tab-host')).toHaveAttribute('data-tool', 'image');
		await screenshot(page, JOURNEY, 'recent-look-editor-1440');
	});

	test('still renders the menu when the browser refuses storage', async ({ page }) => {
		await page.addInitScript(() => {
			const allowed = (key: string) => key.startsWith('auth_') || key === 'potionui_accounts';
			const original = {
				getItem: Storage.prototype.getItem,
				setItem: Storage.prototype.setItem,
				removeItem: Storage.prototype.removeItem
			};
			const refuse = () => {
				throw new DOMException('blocked', 'SecurityError');
			};
			Storage.prototype.getItem = function (key: string) {
				return allowed(key) ? original.getItem.call(this, key) : refuse();
			};
			Storage.prototype.setItem = function (key: string, value: string) {
				return allowed(key) ? original.setItem.call(this, key, value) : refuse();
			};
			Storage.prototype.removeItem = function (key: string) {
				return allowed(key) ? original.removeItem.call(this, key) : refuse();
			};
		});
		await mockBackend(page);
		await openLibraryMenu(page, 'harbour.png');

		expect(await toolsOf(page, 'Effects')).toEqual(['apply-look']);
	});
});

test.describe('the tool catalog fails to load', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await loginAsOwner(page);
	});

	async function mockCatalog(page: Page, state: { healthy: boolean; requests: number }) {
		await mockBackend(page);
		await page.route(/\/api\/media-tools(\?.*)?$/, async (route) => {
			state.requests += 1;
			if (!state.healthy) {
				return route.fulfill({ status: 500, contentType: 'application/json', body: '{"success":false}' });
			}
			return json(route, { tools: CATALOG });
		});
	}

	async function selectHarbour(page: Page) {
		await page.goto('/library');
		await expect(page.locator('[data-library-card]')).toHaveCount(3);
		await page
			.locator('[data-library-card]')
			.filter({ hasText: 'harbour.png' })
			.getByRole('button', { name: 'Select item' })
			.click();
	}

	test('shows one toast, and the next menu open brings the Edit group back', async ({ page }) => {
		const state = { healthy: false, requests: 0 };
		await mockCatalog(page, state);
		await selectHarbour(page);
		await expect(page.getByText("Editing tools couldn't load")).toHaveCount(1);

		state.healthy = true;
		await page.locator('[data-tools-trigger]').first().click();

		await expect.poll(() => groupLabels(page)).toContain('Edit');
		expect(state.requests).toBe(2);
		await expect(page.getByText("Editing tools couldn't load")).toHaveCount(1);
		await screenshot(page, JOURNEY, 'catalog-retry-1440');
	});

	test('retries once only, and stays quiet while the catalog is still down', async ({ page }) => {
		const state = { healthy: false, requests: 0 };
		await mockCatalog(page, state);
		await selectHarbour(page);
		await expect(page.getByText("Editing tools couldn't load")).toHaveCount(1);

		for (let attempt = 0; attempt < 3; attempt += 1) {
			await page.locator('[data-tools-trigger]').first().click();
			await expect(menu(page)).toBeVisible();
			await page.keyboard.press('Escape');
		}

		expect(state.requests).toBe(2);
		await expect(page.getByText("Editing tools couldn't load")).toHaveCount(1);
	});
});

test.describe('Edit and Effects submenus', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await loginAsOwner(page);
		await mockBackend(page);
	});

	test('lists an Edit and an Effects row with counts above the other groups', async ({ page }) => {
		await openLibraryMenu(page, 'harbour.png');

		const first = menu(page).locator('[role="menuitem"]').first();
		await expect(first).toHaveAttribute('data-submenu-row', 'edit');
		await expect(first.locator('[data-submenu-count]')).toHaveText('7');
		await expect(menu(page).locator('[data-submenu-row="effects"] [data-submenu-count]')).toHaveText('1');
		await expect(first).toBeFocused();
	});

	test('opens a row with Enter and with Space, and focuses the first enabled entry', async ({ page }) => {
		await openLibraryMenu(page, 'harbour.png');
		const row = menu(page).locator('[data-submenu-row="edit"]');
		await row.focus();

		await page.keyboard.press('Enter');
		await expect(menu(page).locator('[data-submenu-back]')).toBeVisible();
		await expect(menu(page).locator('[data-tool="crop"]')).toBeFocused();

		await page.keyboard.press('ArrowLeft');
		await expect(row).toBeFocused();
		await page.keyboard.press('Space');
		await expect(menu(page).locator('[data-submenu-back]')).toBeVisible();
		await expect(menu(page).locator('[data-tool="crop"]')).toBeFocused();
	});

	test('opens with ArrowRight and returns with ArrowLeft, Escape and the back row, refocusing the row', async ({ page }) => {
		await openLibraryMenu(page, 'harbour.png');
		const row = menu(page).locator('[data-submenu-row="effects"]');
		await row.focus();

		await page.keyboard.press('ArrowRight');
		await expect(menu(page).locator('[data-tool="apply-look"]')).toBeFocused();
		await page.keyboard.press('ArrowLeft');
		await expect(row).toBeFocused();

		await page.keyboard.press('ArrowRight');
		await page.keyboard.press('Escape');
		await expect(menu(page)).toBeVisible();
		await expect(row).toBeFocused();

		await row.press('ArrowRight');
		await menu(page).locator('[data-submenu-back]').click();
		await expect(row).toBeFocused();
	});

	test('closes the whole menu on Escape at the top level, after Escape left a submenu', async ({ page }) => {
		await openLibraryMenu(page, 'harbour.png');
		await menu(page).locator('[data-submenu-row="edit"]').focus();
		await page.keyboard.press('ArrowRight');

		await page.keyboard.press('Escape');
		await expect(menu(page)).toBeVisible();
		await page.keyboard.press('Escape');

		await expect(menu(page)).toHaveCount(0);
	});

	test('walks a submenu with the arrow keys and opens the focused tool with Enter', async ({ page }) => {
		await openLibraryMenu(page, 'harbour.png');
		await menu(page).locator('[data-submenu-row="edit"]').focus();
		await page.keyboard.press('ArrowRight');
		await expect(menu(page).locator('[data-tool="crop"]')).toBeFocused();

		await page.keyboard.press('ArrowDown');
		await page.keyboard.press('ArrowDown');
		await expect(menu(page).locator('[data-tool="rotate"]')).toBeFocused();
		await page.keyboard.press('Enter');

		await expect(page).toHaveURL(/\/generate/);
		await expect(page.getByTestId('tool-tab-host')).toHaveAttribute('data-tool', 'image');
	});

	test('keeps Analyze, Compose and Export below the two rows, unchanged', async ({ page }) => {
		await openLibraryMenu(page, 'harbour.png');

		const order = await menu(page)
			.locator('[data-submenu-row], [role="group"]')
			.evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-submenu-row') ?? node.getAttribute('aria-label')));
		expect(order.slice(0, 2)).toEqual(['edit', 'effects']);
		expect(order).toEqual(expect.arrayContaining(['Analyze', 'Compose', 'Export']));
	});

	test('shows the same rows and counts on a Library card and a History card', async ({ page }) => {
		await page.goto('/library');
		const libraryCard = page.locator('[data-library-card]').filter({ hasText: 'harbour.png' });
		await libraryCard.hover();
		await libraryCard.getByRole('button', { name: 'Item actions' }).click();
		const libraryRows = await topRows(page);
		await page.keyboard.press('Escape');

		await page.goto('/history');
		const historyCard = page.locator('[data-generation-card]').first();
		await historyCard.hover();
		await historyCard.getByRole('button', { name: 'Item actions' }).click();

		expect(libraryRows).toEqual([
			{ id: 'edit', count: 7 },
			{ id: 'effects', count: 1 }
		]);
		expect(await topRows(page)).toEqual(libraryRows);
		await screenshot(page, JOURNEY, 'history-card-rows-1440');
	});

	test('hides the Effects row on a video card', async ({ page }) => {
		await page.goto('/library');
		const video = page.locator('[data-library-card]').filter({ hasText: 'timelapse.mp4' });
		await video.hover();
		await video.getByRole('button', { name: 'Item actions' }).click();

		expect(await topRows(page)).toEqual([{ id: 'edit', count: 7 }]);
	});
});

test.describe('Edit and Effects submenus on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

	test.beforeEach(async ({ page }) => {
		await loginAsOwner(page);
		await mockBackend(page);
	});

	test('drills into Edit in the bottom sheet and back', async ({ page }) => {
		await page.goto('/library');
		const card = page.locator('[data-library-card]').filter({ hasText: 'harbour.png' });
		await expect(card).toBeVisible();

		await card.getByRole('button', { name: 'Item actions' }).tap();
		const sheet = page.locator('[data-tools-sheet]');
		await expect(sheet).toBeVisible();
		await expect(sheet.locator('[data-submenu-row="edit"] [data-submenu-count]')).toHaveText('7');
		await sheet.screenshot({ path: shotPath(JOURNEY, 'sheet-rows-390'), animations: 'disabled' });

		await sheet.locator('[data-submenu-row="edit"]').tap();
		await expect(sheet.locator('[data-submenu-back]')).toBeVisible();
		await expect(sheet.locator('[data-tool="crop"]')).toBeVisible();
		await sheet.screenshot({ path: shotPath(JOURNEY, 'sheet-edit-390'), animations: 'disabled' });

		await sheet.locator('[data-submenu-back]').tap();
		await expect(sheet.locator('[data-submenu-row="effects"]')).toBeVisible();
	});
});

test.describe('card menu', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await loginAsOwner(page);
		await mockBackend(page);
	});

	test('starts with the same Edit group on a Library card and a History card', async ({ page }) => {
		await page.goto('/library');
		const libraryCard = page.locator('[data-library-card]').filter({ hasText: 'timelapse.mp4' });
		await libraryCard.hover();
		await libraryCard.getByRole('button', { name: 'Item actions' }).click();
		const libraryGroups = await groupLabels(page);
		const libraryEdit = await toolsOf(page, 'Edit');
		await page.keyboard.press('Escape');

		await page.goto('/history');
		const historyCard = page.locator('[data-generation-card]').nth(1);
		await historyCard.hover();
		await historyCard.getByRole('button', { name: 'Item actions' }).click();

		expect(libraryGroups.slice(0, 1)).toEqual(['Edit']);
		expect((await groupLabels(page)).slice(0, 1)).toEqual(['Edit']);
		expect(await toolsOf(page, 'Edit')).toEqual(libraryEdit);
		expect(libraryEdit).not.toContain('concat');
		await screenshot(page, JOURNEY, 'history-card-menu-1440');
	});

	test('a Library image card offers Apply a look under Effects and a video card does not', async ({ page }) => {
		await page.goto('/library');
		const image = page.locator('[data-library-card]').filter({ hasText: 'harbour.png' });
		await image.hover();
		await image.getByRole('button', { name: 'Item actions' }).click();
		expect(await toolsOf(page, 'Effects')).toEqual(['apply-look']);
		await page.keyboard.press('Escape');

		const video = page.locator('[data-library-card]').filter({ hasText: 'timelapse.mp4' });
		await video.hover();
		await video.getByRole('button', { name: 'Item actions' }).click();
		expect(await groupLabels(page)).not.toContain('Effects');
	});
});

test.describe('details action bar', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await loginAsOwner(page);
		await mockBackend(page);
	});

	const bar = (root: Page | Locator) => root.getByRole('toolbar', { name: 'Actions' });
	const barItems = (root: Page | Locator) => bar(root).locator('[data-bar-item]');

	async function openBarMenu(page: Page, name: string) {
		await bar(page).getByRole('button', { name }).click();
		const menuEl = page.getByRole('menu', { name });
		await expect(menuEl).toBeVisible();
		return menuEl;
	}

	const rowLabels = (menuEl: Locator) =>
		menuEl.locator('[data-bar-row-label]').allTextContents();

	test('Library details: one labelled bar above the media', async ({ page }) => {
		await page.goto('/library');
		await page.locator('[data-library-card]').filter({ hasText: 'harbour.png' }).locator('.media-zoom').click();

		await expect(bar(page)).toBeVisible();
		await expect(barItems(page)).toHaveText([/^\s*Edit/, /^\s*Effects/, /^\s*Favorite/, /^\s*Tags/, 'Auto-file…', 'Download', /^\s*More/]);
		await expect(page.locator('[data-library-favorite]')).toHaveAttribute('aria-pressed', 'false');
		const barBox = (await bar(page).boundingBox())!;
		const mediaBox = (await page.getByRole('dialog').locator('img[alt="harbour.png"]').boundingBox())!;
		expect(barBox.y + barBox.height).toBeLessThanOrEqual(mediaBox.y + 1);
		await screenshot(page, JOURNEY, 'library-details-1440');
	});

	test('Library details: Edit lists the tools and Crop to subject says why it is off', async ({ page }) => {
		await page.goto('/library');
		await page.locator('[data-library-card]').filter({ hasText: 'harbour.png' }).locator('.media-zoom').click();

		const edit = await openBarMenu(page, 'Edit');
		expect(await rowLabels(edit)).toEqual([
			'Crop',
			'Resize',
			'Rotate',
			'Flip',
			'Fit canvas',
			'Color key',
			'Crop to subject',
			'Open in Image editor'
		]);
		await expect(edit.locator('[data-bar-row="crop_subject"]')).toHaveAttribute('aria-disabled', 'true');
		await expect(edit.locator('[data-bar-row="crop_subject"]')).toContainText('Needs a transparent image');
		await screenshot(page, JOURNEY, 'library-details-edit-1440');
	});

	test('Library details: Effects offers Apply a look, and Open in Image editor opens a tab', async ({ page }) => {
		await page.goto('/library');
		await page.locator('[data-library-card]').filter({ hasText: 'harbour.png' }).locator('.media-zoom').click();

		const effects = await openBarMenu(page, 'Effects');
		expect(await rowLabels(effects)).toEqual(['Apply a look…']);
		await page.keyboard.press('Escape');
		await expect(effects).toHaveCount(0);

		const edit = await openBarMenu(page, 'Edit');
		await edit.getByRole('menuitem', { name: /Open in Image editor/ }).click();
		await expect(page).toHaveURL(/\/generate/);
		await expect(page.getByTestId('tool-tab-host')).toHaveAttribute('data-tool', 'image');
	});

	test('Library details: More holds Open in new tab, then Delete in danger style', async ({ page }) => {
		await page.goto('/library');
		await page.locator('[data-library-card]').filter({ hasText: 'harbour.png' }).locator('.media-zoom').click();

		const more = await openBarMenu(page, 'More');
		expect(await rowLabels(more)).toEqual(['Open in new tab', 'Delete']);
		await expect(more.locator('[data-bar-row="delete"]')).toHaveClass(/text-danger/);
		await screenshot(page, JOURNEY, 'library-details-more-1440');
	});

	test('History details: the same Edit and Effects, and the generation actions in the bar', async ({ page }) => {
		await page.goto('/history');
		await page.locator('[data-generation-card]').first().locator('.media-zoom').click();

		const dialog = page.getByRole('dialog', { name: 'Generation Details' });
		await expect(bar(dialog)).toBeVisible();
		await expect(barItems(dialog)).toHaveText([
			/^\s*Edit/,
			/^\s*Effects/,
			/^\s*Favorite/,
			/^\s*Tags/,
			'Reuse settings',
			'Copy to Library',
			/^\s*Download/,
			/^\s*More/
		]);
		const edit = await openBarMenu(page, 'Edit');
		expect((await rowLabels(edit)).slice(0, 2)).toEqual(['Crop', 'Resize']);
		await page.keyboard.press('Escape');
		await expect(dialog.locator('[data-stage-toolbar]')).toHaveCount(0);
		await screenshot(page, JOURNEY, 'history-details-1440');
	});

	test('History details: Download holds the file and the bundle, More holds Publish and Open in new tab', async ({
		page
	}) => {
		await page.goto('/history');
		await page.locator('[data-generation-card]').first().locator('.media-zoom').click();

		const download = await openBarMenu(page, 'Download');
		expect(await rowLabels(download)).toEqual(['Download file', 'Export bundle']);
		await page.keyboard.press('Escape');

		const more = await openBarMenu(page, 'More');
		expect(await rowLabels(more)).toEqual(['Publish to Inspirations', 'Open in new tab']);
	});

	test('History details: a video generation can be trimmed, cropped and resized, with no Effects', async ({ page }) => {
		await page.goto('/history');
		await page.locator('[data-generation-card]').nth(1).locator('.media-zoom').click();

		const dialog = page.getByRole('dialog', { name: 'Generation Details' });
		await expect(barItems(dialog)).not.toContainText([/^\s*Effects/]);
		const edit = await openBarMenu(page, 'Edit');
		expect(await rowLabels(edit)).toEqual([
			'Trim',
			'Crop',
			'Resize',
			'Rotate',
			'Flip',
			'Speed',
			'Open frame in Image editor',
			'Open in Video editor'
		]);
	});

	test('History details: on a narrow window Download, Copy to Library and Reuse settings fold into More', async ({
		page
	}) => {
		await page.setViewportSize({ width: 1024, height: 768 });
		await page.goto('/history');
		await page.locator('[data-generation-card]').first().locator('.media-zoom').click();

		const dialog = page.getByRole('dialog', { name: 'Generation Details' });
		await expect(barItems(dialog)).toHaveText([/^\s*Edit/, /^\s*Effects/, /^\s*Favorite/, /^\s*Tags/, /^\s*More/]);
		const more = await openBarMenu(page, 'More');
		expect(await rowLabels(more)).toEqual([
			'Reuse settings',
			'Copy to Library',
			'Download file',
			'Export bundle',
			'Publish to Inspirations',
			'Open in new tab'
		]);
		await screenshot(page, JOURNEY, 'history-details-narrow-more-1024');
	});
});

async function offerDespeckle(page: Page) {
	const despeckle = tool('despeckle', 'Despeckle', {
		accepts: ['image', 'video'],
		preview: true,
		params: [{ name: 'degrees', type: 'select', label: 'Degrees', default: '90', options: ['90', '180', '270'] }]
	});
	await page.route(/\/api\/media-tools(\?.*)?$/, (route) => json(route, { tools: [...CATALOG, despeckle] }));
}

test.describe('running a tool', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await loginAsOwner(page);
	});

	test('shows progress on the card with a cancel button, then the result next to the source tagged New', async ({
		page
	}) => {
		test.setTimeout(120000);
		const backend = await mockBackend(page);
		await offerDespeckle(page);
		await page.goto('/library');
		const card = page.locator('[data-library-card]').filter({ hasText: 'harbour.png' });
		await card.hover();
		await card.getByRole('button', { name: 'Item actions' }).click();
		await openSubmenu(page, 'edit');
		await menu(page).locator('[data-tool="despeckle"]').click();

		const dialog = page.getByRole('dialog', { name: /Despeckle/ });
		await expect(dialog).toBeVisible();
		await dialog.getByRole('button', { name: 'Apply' }).click();

		const band = card.locator('[data-tool-run]');
		await expect(band).toBeVisible();
		await expect(band.locator('[data-tool-run-percent]')).toHaveText('42%');
		await expect(band.getByRole('button', { name: /Cancel/ })).toBeVisible();
		expect(backend.runs.started).toHaveLength(1);
		await screenshot(page, JOURNEY, 'running-1440');

		backend.library = [libraryRowAfter(), ...backend.library];
		backend.runs.finish = true;
		await expect(band).toHaveCount(0, { timeout: 15000 });

		const added = page.locator('[data-library-card="lib-new"]');
		await expect(added).toBeVisible();
		await expect(added.locator('[data-new-tag]')).toHaveText('New');
		const order = await page
			.locator('[data-library-card]')
			.evaluateAll((cards) => cards.map((node) => node.getAttribute('data-library-card')));
		expect(order.indexOf('lib-new')).toBe(order.indexOf('lib-img') - 1);
		await screenshot(page, JOURNEY, 'finished-1440');
	});

	test('cancelling from the card stops the run', async ({ page }) => {
		const backend = await mockBackend(page);
		await offerDespeckle(page);
		await page.goto('/library');
		const card = page.locator('[data-library-card]').filter({ hasText: 'harbour.png' });
		await card.hover();
		await card.getByRole('button', { name: 'Item actions' }).click();
		await openSubmenu(page, 'edit');
		await menu(page).locator('[data-tool="despeckle"]').click();
		await page.getByRole('dialog', { name: /Despeckle/ }).getByRole('button', { name: 'Apply' }).click();

		await card.locator('[data-tool-run]').getByRole('button', { name: /Cancel/ }).click();

		await expect.poll(() => backend.runs.cancelled).toEqual(['run-1']);
		await expect(card.locator('[data-tool-run]')).toHaveCount(0);
	});

	test('a run started from the History card menu opens the result in a Library modal over History', async ({ page }) => {
		test.setTimeout(120000);
		const backend = await mockBackend(page);
		await offerDespeckle(page);
		await page.goto('/history');
		const card = page.locator('[data-generation-card]').first();
		await card.hover();
		await card.getByRole('button', { name: 'Item actions' }).click();
		await openSubmenu(page, 'edit');
		await menu(page).locator('[data-tool="despeckle"]').click();
		const dialog = page.getByRole('dialog', { name: /Despeckle/ });
		await expect(dialog).toContainText('Saves a copy to your Library. The generation is not changed.');
		await dialog.getByRole('button', { name: 'Apply' }).click();

		await expect(card.locator('[data-tool-run]')).toBeVisible();
		backend.runs.finish = true;

		await expect(page.getByRole('dialog', { name: 'harbour-rotated.png' })).toBeVisible({ timeout: 15000 });
		await expect(page).toHaveURL(/\/history/);
		await expect(page.getByRole('status').filter({ hasText: 'Saved to Library' })).toHaveCount(0);
	});

	async function openDetailsOf(page: Page, id: string) {
		await page.goto('/history');
		const cards = page.locator('[data-generation-card]');
		await expect(cards).toHaveCount(2);
		const details = page.getByRole('dialog', { name: 'Generation Details' });
		for (let position = 0; position < 2; position += 1) {
			await cards.nth(position).locator('.media-zoom').click();
			await expect(details).toBeVisible();
			if ((await details.locator('code').first().innerText()).trim() === id) return details;
			await page.keyboard.press('Escape');
			await expect(details).toHaveCount(0);
		}
		return details;
	}

	test('History details: Edit, Rotate and Apply close the details and open the result over History, and Back to the generation reopens them', async ({
		page
	}) => {
		test.setTimeout(120000);
		const backend = await mockBackend(page);
		await offerDespeckle(page);
		const details = await openDetailsOf(page, 'gen-1');

		await page.getByRole('toolbar', { name: 'Actions' }).getByRole('button', { name: 'Edit' }).click();
		await page.getByRole('menuitem', { name: /Despeckle/ }).click();
		await expect(details).toHaveCount(0);
		const dialog = page.getByRole('dialog', { name: /Despeckle/ });
		await dialog.getByRole('button', { name: 'Apply' }).click();

		backend.runs.finish = true;

		const result = page.getByRole('dialog', { name: 'harbour-rotated.png' });
		await expect(result).toBeVisible({ timeout: 15000 });
		await expect(page).toHaveURL(/\/history/);
		await result.getByRole('button', { name: 'Back to the generation' }).click();

		await expect(result).toHaveCount(0);
		await expect(details).toBeVisible();
		await expect(details.locator('code').first()).toHaveText('gen-1');
		await expect(page).toHaveURL(/\/history/);
	});

	test('History details: browser Back closes the result modal and reopens the details', async ({ page }) => {
		test.setTimeout(120000);
		const backend = await mockBackend(page);
		await offerDespeckle(page);
		const details = await openDetailsOf(page, 'gen-1');

		await page.getByRole('toolbar', { name: 'Actions' }).getByRole('button', { name: 'Edit' }).click();
		await page.getByRole('menuitem', { name: /Despeckle/ }).click();
		await page.getByRole('dialog', { name: /Despeckle/ }).getByRole('button', { name: 'Apply' }).click();
		backend.runs.finish = true;
		const result = page.getByRole('dialog', { name: 'harbour-rotated.png' });
		await expect(result).toBeVisible({ timeout: 15000 });

		await page.goBack();

		await expect(result).toHaveCount(0);
		await expect(details).toBeVisible();
		await expect(page).toHaveURL(/\/history/);
	});

	test('History details: Cancel in the tool dialog reopens the details on the same generation', async ({ page }) => {
		test.setTimeout(120000);
		await mockBackend(page);
		await offerDespeckle(page);
		const details = await openDetailsOf(page, 'gen-1');

		await page.getByRole('toolbar', { name: 'Actions' }).getByRole('button', { name: 'Edit' }).click();
		await page.getByRole('menuitem', { name: /Despeckle/ }).click();
		await expect(details).toHaveCount(0);
		await page.getByRole('dialog', { name: /Despeckle/ }).getByRole('button', { name: 'Cancel' }).click();

		await expect(details).toBeVisible();
		await expect(details.locator('code').first()).toHaveText('gen-1');
	});

	test('History details: a failed run reopens the details and offers Try again', async ({ page }) => {
		test.setTimeout(120000);
		const backend = await mockBackend(page);
		await offerDespeckle(page);
		const details = await openDetailsOf(page, 'gen-1');

		await page.getByRole('toolbar', { name: 'Actions' }).getByRole('button', { name: 'Edit' }).click();
		await page.getByRole('menuitem', { name: /Despeckle/ }).click();
		await page.getByRole('dialog', { name: /Despeckle/ }).getByRole('button', { name: 'Apply' }).click();
		backend.runs.fail = true;

		const toast = page.getByRole('status').filter({ hasText: 'Despeckle failed. Your settings are kept.' });
		await expect(toast).toBeVisible({ timeout: 15000 });
		await expect(details).toBeVisible();
		await toast.getByRole('button', { name: 'Try again' }).click();
		await expect(page.getByRole('dialog', { name: /Despeckle/ })).toBeVisible();
	});

	test('a tool opened from the History card menu does not reopen anything when it is cancelled', async ({ page }) => {
		await mockBackend(page);
		await offerDespeckle(page);
		await page.goto('/history');
		const card = page.locator('[data-generation-card]').first();
		await card.hover();
		await card.getByRole('button', { name: 'Item actions' }).click();
		await openSubmenu(page, 'edit');
		await menu(page).locator('[data-tool="despeckle"]').click();
		await page.getByRole('dialog', { name: /Despeckle/ }).getByRole('button', { name: 'Cancel' }).click();

		await expect(page.getByRole('dialog')).toHaveCount(0);
	});

	test('/library?item=<id> opens an item that is not on the current page, and clears a missing one', async ({
		page
	}) => {
		await mockBackend(page);
		await offerDespeckle(page);
		await page.goto('/library?item=lib-new');
		await expect(page.getByRole('dialog', { name: 'harbour-rotated.png' })).toBeVisible({ timeout: 15000 });
		await page.keyboard.press('Escape');
		await expect(page).not.toHaveURL(/item=/);
		await page.reload();
		await expect(page.getByRole('dialog')).toHaveCount(0);

		await page.goto('/library?item=lib-ghost');
		await expect(page.getByText("That library item couldn't be found")).toBeVisible({ timeout: 15000 });
		await expect(page).not.toHaveURL(/item=/);
	});
});

function libraryRowAfter() {
	return {
		...libraryRow('lib-new', 'harbour-rotated.png', 'image'),
		filename: 'lib-new-file.png',
		url: PNG_URL,
		created_at: new Date().toISOString(),
		tags: [],
		is_favorite: false
	};
}
