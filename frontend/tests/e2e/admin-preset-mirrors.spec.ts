import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'admin-preset-mirrors';

const VIEWPORTS = [
	{ name: '1440', width: 1440, height: 900 },
	{ name: '390', width: 390, height: 844 }
] as const;

const MODES = [
	{ name: 'txt2img', label: 'Text to Image', icon: 'image' },
	{ name: 'img2img', label: 'Image to Image', icon: 'image' },
	{ name: 'video', label: 'Video', icon: 'video' }
];

const SOURCE_VARS = { video_director: { modes: { t2v: {} }, preset_modes: ['video'] } };

interface Entry {
	id: string;
	name: string;
	version: string;
	description?: string;
	tags: string[];
	category?: string;
	engine?: string | null;
	installed: boolean;
	preset_db_id?: string;
	assignment_count?: number;
	group_count?: number;
	recipes: unknown[];
	modes: string[];
	media: { cover?: string | null } | null;
	file_name?: string;
	display_overridden?: boolean;
	is_mirror?: boolean;
	source_preset_id?: string | null;
	mirror_state?: string | null;
	mirror_count?: number;
	engine_available?: boolean;
}

function source(): Entry {
	return {
		id: 'krea2',
		name: 'Realistic',
		version: '1.2.0',
		description: 'Fast photoreal images.',
		tags: ['photo'],
		category: 'image',
		engine: 'native',
		installed: true,
		preset_db_id: '01DBKREA',
		assignment_count: 2,
		group_count: 1,
		recipes: [],
		modes: MODES.map((mode) => mode.name),
		media: { cover: null },
		file_name: 'Krea-2',
		display_overridden: true,
		is_mirror: false,
		source_preset_id: null,
		mirror_state: null,
		mirror_count: 2
	};
}

function mirror(id: string, name: string, offered: string[]): Entry {
	return {
		...source(),
		id,
		name,
		description: 'Fast photoreal images.',
		preset_db_id: `DB${id}`,
		assignment_count: 0,
		group_count: 0,
		modes: offered,
		display_overridden: true,
		is_mirror: true,
		source_preset_id: 'krea2',
		mirror_state: 'ok',
		mirror_count: 0
	};
}

function brokenMirror(): Entry {
	return {
		id: '01BROKEN',
		name: 'Old Portraits',
		version: '',
		tags: [],
		engine: null,
		installed: true,
		preset_db_id: 'DB01BROKEN',
		assignment_count: 1,
		group_count: 0,
		recipes: [],
		modes: [],
		media: null,
		is_mirror: true,
		source_preset_id: 'sdxl-portraits',
		mirror_state: 'source_missing',
		mirror_count: 0,
		engine_available: false
	};
}

function plainPreset(): Entry {
	return {
		id: 'zimage',
		name: 'Z-Image',
		version: '0.4.0',
		tags: [],
		category: 'image',
		engine: 'native',
		installed: false,
		recipes: [],
		modes: ['txt2img'],
		media: { cover: null },
		file_name: 'Z-Image',
		is_mirror: false,
		mirror_state: null,
		mirror_count: 0
	};
}

interface Mocks {
	entries: Entry[];
	displayPuts: unknown[];
	modesPuts: unknown[];
	mirrorPosts: unknown[];
	fixedPuts: unknown[];
	uninstallMode: 'has_mirrors' | 'legacy' | 'ok';
	fixedPromptError: boolean;
}

function adminDetail(mocks: Mocks, id: string) {
	const entry = mocks.entries.find((item) => item.id === id);
	if (!entry) return null;
	const isMirror = !!entry.is_mirror;
	const src = source();
	const sourceAlive = mocks.entries.some((item) => item.id === entry.source_preset_id);
	return {
		preset_id: entry.id,
		preset_db_id: entry.preset_db_id ?? `DB${entry.id}`,
		is_mirror: isMirror,
		source_preset_id: entry.source_preset_id ?? null,
		source_name: isMirror ? (sourceAlive ? src.name : null) : null,
		mirror_state: entry.mirror_state ?? null,
		file: isMirror && !sourceAlive ? null : { name: 'Krea-2', description: 'Fast photoreal images.', cover: null },
		display: {
			name: isMirror || entry.display_overridden ? entry.name : null,
			description: null,
			cover: null
		},
		effective: { name: entry.name, description: entry.description ?? null, cover: null },
		modes: {
			available: isMirror && !sourceAlive ? [] : MODES,
			offered: isMirror ? entry.modes : null,
			first: isMirror ? (entry.modes[0] ?? null) : null
		},
		mirrors: isMirror
			? []
			: mocks.entries
					.filter((item) => item.source_preset_id === entry.id)
					.map((item) => ({ preset_id: item.id, name: item.name, mirror_state: item.mirror_state ?? 'ok' })),
		assignment_count: entry.assignment_count ?? 0,
		group_count: entry.group_count ?? 0
	};
}

async function json(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
}

async function refuse(route: Route, status: number, body: Record<string, unknown>) {
	await json(route, { detail: { success: false, ...body } }, status);
}

async function mockPresets(page: Page): Promise<Mocks> {
	const mocks: Mocks = {
		entries: [source(), mirror('01MIRROR', 'Film Covers', ['txt2img']), mirror('01ANIME', 'Anime', ['txt2img', 'img2img']), brokenMirror(), plainPreset()],
		displayPuts: [],
		modesPuts: [],
		mirrorPosts: [],
		fixedPuts: [],
		uninstallMode: 'has_mirrors',
		fixedPromptError: false
	};

	await page.route(
		(url) => url.pathname === '/api/presets' || url.pathname.startsWith('/api/presets/'),
		async (route) => {
			const request = route.request();
			const url = new URL(request.url());
			const method = request.method();
			const parts = url.pathname.split('/').filter(Boolean).slice(2);

			if (parts.length === 0) {
				if (url.searchParams.get('include_uninstalled') === 'true') return json(route, { success: true, data: mocks.entries });
				return route.fallback();
			}
			const [id, action, sub] = parts;
			if (['picker-state', 'mode-labels'].includes(id)) return route.fallback();
			const entry = mocks.entries.find((item) => item.id === id);
			if (!entry) return route.fallback();

			if (!action) {
				if (entry.mirror_state && entry.mirror_state !== 'ok') {
					return json(route, { success: false, error: 'preset_not_found', message: 'Preset not found' }, 404);
				}
				return json(route, {
					success: true,
					data: { ...entry, media: { cover: null, gallery: [] }, styles: [], vars: entry.is_mirror ? {} : SOURCE_VARS }
				});
			}
			if (action === 'admin') return json(route, { success: true, data: adminDetail(mocks, id) });
			if (action === 'modes' && method === 'GET') {
				const modes = MODES.filter((mode) => entry.modes.includes(mode.name)).map((mode) => ({ ...mode, variants: [] }));
				return json(route, { success: true, data: { preset_id: id, modes, default_mode: modes[0]?.name ?? '' } });
			}
			if (action === 'modes' && method === 'PUT') {
				const body = request.postDataJSON();
				mocks.modesPuts.push(body);
				entry.modes = body.offered ?? MODES.map((mode) => mode.name);
				return json(route, { success: true, data: adminDetail(mocks, id) });
			}
			if (action === 'configuration') return json(route, { success: true, data: { preset_id: id, entries: [] } });
			if (action === 'assignments') return json(route, { success: true, data: { preset_db_id: entry.preset_db_id, assignments: [] } });
			if (action === 'requirements') {
				return json(route, {
					success: true,
					data: { results: [], summary: { ok: 0, missing: 0, unknown: 0, optional_missing: 0 }, backends: [], checked_at: 0 }
				});
			}
			if (action === 'display' && sub === 'cover') {
				return json(route, { success: true, data: adminDetail(mocks, id) });
			}
			if (action === 'display' && method === 'PUT') {
				const body = request.postDataJSON();
				mocks.displayPuts.push(body);
				if (body.name) entry.name = body.name;
				const detail = adminDetail(mocks, id)!;
				return json(route, { success: true, data: { ...detail, display: body } });
			}
			if (action === 'fixed-prompt' && method === 'GET') {
				return json(route, {
					success: true,
					data: entry.is_mirror
						? { prefix: 'cinematic film still,', suffix: '35mm grain', negative: 'text, watermark', modes: null }
						: { prefix: '', suffix: '', negative: '', modes: null }
				});
			}
			if (action === 'fixed-prompt' && method === 'PUT') {
				const body = request.postDataJSON();
				mocks.fixedPuts.push(body);
				if (mocks.fixedPromptError) {
					return refuse(route, 400, {
						error: 'invalid_fixed_prompt',
						message: 'The prompt text is not valid.',
						errors: ['prefix: a { has no closing }']
					});
				}
				return json(route, { success: true, data: body });
			}
			if (action === 'mirrors' && method === 'POST') {
				const body = request.postDataJSON();
				mocks.mirrorPosts.push(body);
				const created = mirror('01CREATED', body.name, body.offered_modes ?? MODES.map((mode) => mode.name));
				mocks.entries.push(created);
				return json(route, { success: true, data: adminDetail(mocks, created.id) }, 201);
			}
			if (action === 'uninstall') {
				if (mocks.uninstallMode === 'has_mirrors') {
					return refuse(route, 409, {
						error: 'preset_has_mirrors',
						message: 'Remove its mirrors first.',
						mirrors: mocks.entries
							.filter((item) => item.source_preset_id === id)
							.map((item) => ({ preset_id: item.id, name: item.name }))
					});
				}
				if (mocks.uninstallMode === 'legacy') {
					return json(route, { success: false, error: 'preset_in_use', message: 'A recipe still needs this preset.' });
				}
				mocks.entries = mocks.entries.filter((item) => item.id !== id);
				return json(route, { success: true, message: 'Removed', data: { tombstone: { preset_id: id, name: entry.name } } });
			}
			return route.fallback();
		}
	);
	return mocks;
}

async function openAdmin(page: Page, query: string) {
	await page.goto(`/admin?tab=presets${query}`);
}

test('a preset’s Display section saves a rename and clears back to the file value', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	const mocks = await mockPresets(page);
	await loginAsOwner(page);
	await openAdmin(page, '&id=krea2&view=display');

	const name = page.locator('#preset-display-name');
	await expect(name).toBeVisible({ timeout: 20000 });
	await expect(name).toHaveAttribute('placeholder', 'Krea-2');
	await expect(name).toHaveValue('Realistic');
	await expect(page.locator('[data-detail-subtitle]')).toContainText('Renamed from Krea-2');

	await name.fill('Studio Portraits');
	await expect(page.getByText('1 unsaved change')).toBeVisible();
	await screenshot(page, JOURNEY, 'display-dirty-1440');
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await expect.poll(() => mocks.displayPuts.length).toBe(1);
	expect(mocks.displayPuts[0]).toEqual({ name: 'Studio Portraits', description: null, cover: null });
	await expect(page.getByText('1 unsaved change')).toHaveCount(0);

	await name.fill('');
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await expect.poll(() => mocks.displayPuts.length).toBe(2);
	expect(mocks.displayPuts[1]).toEqual({ name: null, description: null, cover: null });

	await page.getByRole('button', { name: 'Change cover' }).click();
	await expect(page.locator('[data-preset-display-tab]')).toBeVisible();
	await screenshot(page, JOURNEY, 'display-cover-picker-1440');

	await page.setViewportSize({ width: 390, height: 844 });
	await screenshot(page, JOURNEY, 'display-390');

	await page.setViewportSize({ width: 1440, height: 900 });
	await openAdmin(page, '&id=krea2&view=prompt');
	await expect(page.locator('[data-fixed-prompt-input="prefix"]')).toBeVisible({ timeout: 20000 });
	await expect(page.locator('[data-director-note]')).toContainText('Video');
	await screenshot(page, JOURNEY, 'source-prompt-text-1440');
});

test('an admin creates a mirror with one mode and lands on its Access tab', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	const mocks = await mockPresets(page);
	await loginAsOwner(page);
	await openAdmin(page, '&id=krea2');

	await expect(page.locator('[data-preset-mirrors-list]')).toContainText('Film Covers', { timeout: 20000 });
	await page.getByRole('button', { name: 'More actions' }).click();
	await page.getByRole('menuitem', { name: 'Create mirror' }).click();

	const dialog = page.getByRole('dialog');
	await expect(dialog.locator('[data-create-mirror-form]')).toBeVisible();
	await expect(dialog.getByRole('button', { name: 'Create mirror', exact: true })).toBeDisabled();
	await dialog.locator('#create-mirror-name').fill('Cover Art');
	await dialog.getByRole('button', { name: 'Image to Image' }).click();
	await dialog.getByRole('button', { name: 'Video' }).click();
	await screenshot(page, JOURNEY, 'create-mirror-1440');

	await page.setViewportSize({ width: 390, height: 844 });
	await screenshot(page, JOURNEY, 'create-mirror-390');
	await page.setViewportSize({ width: 1440, height: 900 });

	await dialog.getByRole('button', { name: 'Create mirror', exact: true }).click();
	await expect.poll(() => mocks.mirrorPosts.length).toBe(1);
	expect(mocks.mirrorPosts[0]).toEqual({
		name: 'Cover Art',
		description: null,
		cover: null,
		offered_modes: ['txt2img'],
		first_mode: 'txt2img'
	});
	await expect(page).toHaveURL(/id=01CREATED/, { timeout: 15000 });
	await expect(page).toHaveURL(/view=access/);
	await expect(page.locator('[data-detail-title]')).toHaveText('Cover Art');
	await expect(page.locator('[data-detail-subtitle]')).toContainText('Mirror of');
});

test('a mirror’s detail edits its modes and shows fixed-prompt errors next to the field', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	const mocks = await mockPresets(page);
	await loginAsOwner(page);
	await openAdmin(page, '&id=01ANIME&view=display');

	const modes = page.locator('[data-mirror-modes]');
	await expect(modes).toBeVisible({ timeout: 20000 });
	await expect(page.locator('[data-detail-subtitle]')).toContainText('Mirror of');
	await expect(page.locator('[data-mirror-source-link]')).toHaveText('Realistic');
	await page.locator('#mirror-first-mode').selectOption('img2img');
	await expect(page.getByText('1 unsaved change')).toBeVisible();
	await screenshot(page, JOURNEY, 'mirror-display-1440');
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await expect.poll(() => mocks.modesPuts.length).toBe(1);
	expect(mocks.modesPuts[0]).toEqual({ offered: ['txt2img', 'img2img'], first: 'img2img' });

	await page.setViewportSize({ width: 390, height: 844 });
	await screenshot(page, JOURNEY, 'mirror-display-390');
	await page.setViewportSize({ width: 1440, height: 900 });

	await openAdmin(page, '&id=01ANIME&view=prompt');
	const prefix = page.locator('[data-fixed-prompt-input="prefix"]');
	await expect(prefix).toHaveValue('cinematic film still,', { timeout: 20000 });
	mocks.fixedPromptError = true;
	await prefix.fill('cinematic {film|photo still,');
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await expect.poll(() => mocks.fixedPuts.length).toBe(1);
	await expect(page.getByText('A { has no closing }', { exact: true })).toBeVisible();
	await expect(page.getByText('prefix: a { has no closing }')).toHaveCount(0);
	await screenshot(page, JOURNEY, 'mirror-prompt-error-1440');

	await page.setViewportSize({ width: 390, height: 844 });
	await screenshot(page, JOURNEY, 'mirror-prompt-error-390');
});

for (const viewport of VIEWPORTS) {
	test(`the library shows mirrors, their sources and a broken mirror in plain words at ${viewport.name}`, async ({ page }) => {
		await page.setViewportSize({ width: viewport.width, height: viewport.height });
		await mockPresets(page);
		await loginAsOwner(page);
		await openAdmin(page, '');

		const catalog = page.locator('[role="list"][aria-label="Preset catalog"]');
		await expect(catalog.locator('[data-preset-card]').first()).toBeVisible({ timeout: 20000 });
		const film = catalog.locator('[data-preset-card]').filter({ hasText: 'Film Covers' });
		await expect(film).toContainText('Mirror of Realistic');
		await expect(catalog.locator('[data-preset-card]').filter({ hasText: '2 mirrors' })).toHaveCount(1);
		const broken = catalog.locator('[data-preset-mirror="source_missing"]');
		await expect(broken).toContainText('Source missing');
		await expect(broken).not.toContainText('source_missing');
		await screenshot(page, JOURNEY, `library-${viewport.name}`);

		await broken.getByRole('button', { name: 'Open' }).click();
		await expect(page.locator('[data-mirror-state-alert]')).toBeVisible({ timeout: 15000 });
		await expect(page.locator('[data-mirror-state-alert]').getByRole('button', { name: 'Remove mirror' })).toBeVisible();
		await screenshot(page, JOURNEY, `broken-mirror-${viewport.name}`);
	});
}

test('removing a source with mirrors names them, and an older refusal still shows its message', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	const mocks = await mockPresets(page);
	await loginAsOwner(page);
	await openAdmin(page, '&id=krea2');

	await expect(page.locator('[data-detail-title]')).toHaveText('Realistic', { timeout: 20000 });
	await page.getByRole('button', { name: 'More actions' }).click();
	await page.getByRole('menuitem', { name: 'Uninstall' }).click();
	await page.getByRole('alertdialog').getByRole('button', { name: 'Uninstall' }).click();

	const blocked = page.locator('[data-preset-has-mirrors]');
	await expect(blocked).toBeVisible({ timeout: 15000 });
	await expect(blocked).toContainText('Remove its mirrors first');
	await expect(blocked.locator('[data-has-mirrors-item]')).toHaveCount(2);
	await expect(blocked).toContainText('Film Covers');
	await expect(blocked).toContainText('Anime');
	await screenshot(page, JOURNEY, 'has-mirrors-1440');
	await page.setViewportSize({ width: 390, height: 844 });
	await screenshot(page, JOURNEY, 'has-mirrors-390');
	await page.setViewportSize({ width: 1440, height: 900 });

	await blocked.locator('[data-has-mirrors-item]').filter({ hasText: 'Film Covers' }).getByRole('button', { name: 'Open' }).click();
	await expect(page).toHaveURL(/id=01MIRROR/, { timeout: 15000 });
	await expect(blocked).toHaveCount(0);

	mocks.uninstallMode = 'legacy';
	await openAdmin(page, '&id=krea2');
	await expect(page.locator('[data-detail-title]')).toHaveText('Realistic', { timeout: 20000 });
	await page.getByRole('button', { name: 'More actions' }).click();
	await page.getByRole('menuitem', { name: 'Uninstall' }).click();
	await page.getByRole('alertdialog').getByRole('button', { name: 'Uninstall' }).click();
	await expect(page.getByText('A recipe still needs this preset.')).toBeVisible({ timeout: 15000 });
	await expect(page.locator('[data-preset-has-mirrors]')).toHaveCount(0);
});
