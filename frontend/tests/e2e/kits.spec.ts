import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'kits';

const PNG = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
	'base64'
);
const PNG_URL = `data:image/png;base64,${PNG.toString('base64')}`;

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

interface Backend {
	kits: WireKit[];
	submitted: Array<Record<string, unknown>>;
	notices: Array<Record<string, unknown>>;
	counter: number;
}

const NOW = '2026-11-12T08:00:00Z';

function media(id: string) {
	return {
		upload_id: id,
		filename: `${id}.png`,
		original_filename: `${id}.png`,
		media_type: 'image',
		url: PNG_URL,
		thumbnail_small: PNG_URL,
		thumbnail_medium: PNG_URL,
		width: 96,
		height: 96,
		duration_seconds: null,
		size: 70
	};
}

function counts(kit: WireKit) {
	const result = { image: 0, video: 0, audio: 0, text: 0 };
	for (const item of kit.items) result[item.kind] += 1;
	return result;
}

function detail(kit: WireKit) {
	return { ...kit, position: 0, counts: counts(kit), item_count: kit.items.length, created_at: NOW, updated_at: NOW };
}

function summary(kit: WireKit) {
	const { items, ...rest } = detail(kit);
	return {
		...rest,
		thumbnails: items.filter((item) => item.media).slice(0, 4).map((item) => ({ item_id: item.id, kind: item.kind, url: PNG_URL, thumbnail_small: PNG_URL, thumbnail_medium: PNG_URL }))
	};
}

function newItem(backend: Backend, kit: WireKit, kind: WireItem['kind'], name: string | undefined, text: string | null): WireItem {
	backend.counter += 1;
	const taken = new Set(kit.items.map((item) => item.name));
	let n = 1;
	while (!name && taken.has(`${kind}${n}`)) n += 1;
	const id = `it_${backend.counter.toString(16).padStart(8, '0')}`;
	return {
		id,
		kit_id: kit.id,
		name: name ?? `${kind}${n}`,
		kind,
		position: kit.items.length,
		text,
		media: kind === 'text' ? null : media(`up_${backend.counter}`),
		missing: false,
		created_at: NOW
	};
}

async function json(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

async function conflict(route: Route, suggestion: string) {
	await route.fulfill({
		status: 409,
		contentType: 'application/json',
		body: JSON.stringify({ detail: { success: false, error: 'item_path_taken', message: `Already used, try '${suggestion}'`, suggestion } })
	});
}

async function mockKits(page: Page, seed: WireKit[] = []): Promise<Backend> {
	const backend: Backend = { kits: seed, submitted: [], notices: [], counter: 100 };
	const find = (id: string) => backend.kits.find((kit) => kit.id === id);

	await page.route(/\/api\/kits(\/.*)?(\?.*)?$/, async (route) => {
		const request = route.request();
		const url = new URL(request.url());
		const path = url.pathname.replace(/^\/api\/kits\/?/, '');
		const method = request.method();
		const body = (request.postDataJSON?.() ?? {}) as Record<string, any>;

		if (path === '' && method === 'GET') {
			const label = url.searchParams.get('label');
			const list = backend.kits.filter((kit) => !label || kit.label === label);
			const labels = [...new Set(backend.kits.map((kit) => kit.label).filter(Boolean))].map((name) => ({
				label: name,
				count: backend.kits.filter((kit) => kit.label === name).length
			}));
			return json(route, { kits: list.map(summary), total: list.length, labels });
		}
		if (path === '' && method === 'POST') {
			backend.counter += 1;
			const handle = String(body.name).toLowerCase().replace(/\s+/g, '_');
			const kit: WireKit = { id: `kt_${backend.counter.toString(16).padStart(8, '0')}`, name: body.name, handle, label: body.label ?? null, items: [] };
			backend.kits.push(kit);
			return json(route, { kit: detail(kit) });
		}
		if (path === 'add-library-items' && method === 'POST') {
			let kit = body.kit_id ? find(body.kit_id) : undefined;
			if (!kit) {
				backend.counter += 1;
				kit = { id: `kt_${backend.counter.toString(16).padStart(8, '0')}`, name: body.new_kit.name, handle: body.new_kit.name.toLowerCase().replace(/\s+/g, '_'), label: body.new_kit.label ?? null, items: [] };
				backend.kits.push(kit);
			}
			for (const uploadId of body.upload_ids as string[]) kit.items.push(newItem(backend, kit, 'image', body.names?.[uploadId], null));
			return json(route, { kit: detail(kit), added: [] });
		}
		if (path === 'add-history-items' && method === 'POST') {
			let kit = body.kit_id ? find(body.kit_id) : undefined;
			if (!kit) {
				backend.counter += 1;
				kit = { id: `kt_${backend.counter.toString(16).padStart(8, '0')}`, name: body.new_kit.name, handle: body.new_kit.name.toLowerCase().replace(/\s+/g, '_'), label: body.new_kit.label ?? null, items: [] };
				backend.kits.push(kit);
			}
			backend.submitted.push(body);
			for (const fileId of body.file_ids as string[]) kit.items.push(newItem(backend, kit, 'image', body.names?.[fileId], null));
			return json(route, { kit: detail(kit), added: [] });
		}
		if (path === 'usage') return json(route, { usage: {} });
		if (path === 'resolve') {
			return json(route, { field: 'references', items: [], skipped: backend.notices, expanded: '', text: String(body.text ?? ''), takes: { image: true, video: true, audio: false, text: true }, limits: {} });
		}
		const [kitId, section, itemId] = path.split('/');
		const kit = find(kitId);
		if (!kit) return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: { success: false, error: 'kit_not_found', message: 'Kit not found' } }) });
		if (!section && method === 'GET') return json(route, { kit: detail(kit) });
		if (!section && method === 'PATCH') {
			if (body.name !== undefined) kit.name = body.name;
			if (body.handle !== undefined) kit.handle = body.handle;
			if ('label' in body) kit.label = body.label;
			return json(route, { kit: detail(kit) });
		}
		if (!section && method === 'DELETE') {
			backend.kits = backend.kits.filter((entry) => entry.id !== kit.id);
			return json(route, { id: kit.id, deleted: true });
		}
		if (section === 'usage') return json(route, { total: 0, sessions: 0, films: 0, tabs: [] });
		if (section === 'duplicate') {
			backend.counter += 1;
			const copy: WireKit = { ...kit, id: `kt_${backend.counter.toString(16).padStart(8, '0')}`, name: `${kit.name} copy`, handle: `${kit.handle}_copy`, items: kit.items.map((item) => ({ ...item })) };
			backend.kits.push(copy);
			return json(route, { kit: detail(copy) });
		}
		if (section === 'items' && itemId === 'upload' && method === 'POST') {
			kit.items.push(newItem(backend, kit, 'image', undefined, null));
			return json(route, { kit: detail(kit) });
		}
		if (section === 'items' && !itemId && method === 'POST') {
			kit.items.push(newItem(backend, kit, body.text !== undefined ? 'text' : 'image', body.name, body.text ?? null));
			return json(route, { kit: detail(kit) });
		}
		if (section === 'items' && itemId && method === 'PATCH') {
			const item = kit.items.find((entry) => entry.id === itemId)!;
			if (body.name !== undefined) {
				if (kit.items.some((entry) => entry.id !== itemId && entry.name === body.name)) return conflict(route, `${body.name}2`);
				item.name = body.name;
			}
			if (body.text !== undefined) item.text = body.text;
			return json(route, { kit: detail(kit) });
		}
		if (section === 'items' && itemId && method === 'DELETE') {
			kit.items = kit.items.filter((entry) => entry.id !== itemId);
			return json(route, { kit: detail(kit) });
		}
		if (section === 'order') {
			const ids = body.ids as string[];
			kit.items = ids.map((id) => kit.items.find((item) => item.id === id)!);
			return json(route, { kit: detail(kit) });
		}
		return route.fallback();
	});
	return backend;
}

function seedMara(): WireKit {
	const id = 'kt_7f3a1c2e';
	const item = (suffix: string, kind: WireItem['kind'], name: string, text: string | null): WireItem => ({
		id: `it_41c9ab${suffix}`,
		kit_id: id,
		name,
		kind,
		position: 0,
		text,
		media: kind === 'text' ? null : media(`up_${suffix}`),
		missing: false,
		created_at: NOW
	});
	return { id, name: 'Mara', handle: 'mara', label: 'Actor', items: [item('01', 'image', 'face', null), item('02', 'audio', 'voice', null), item('03', 'text', 'look', 'Warm and calm.')] };
}

async function mockLibrary(page: Page, count = 2) {
	const rows = Array.from({ length: count }, (_, index) => `upload-${index + 1}.png`).map((name, index) => ({
		id: `lib-${index + 1}`,
		filename: `lib-${index + 1}.png`,
		original_filename: name,
		media_type: 'image',
		url: PNG_URL,
		thumbnail_medium: PNG_URL,
		width: 96,
		height: 96,
		tags: [],
		is_favorite: false,
		created_at: NOW
	}));
	await page.route(/\/api\/library\/items(\?.*)?$/, (route) => json(route, { items: rows, total: rows.length, limit: 24, offset: 0 }));
	await page.route(/\/api\/collections\/?(\?.*)?$/, (route) => json(route, { collections: [], smart_counts: { all: rows.length, favorites: 0, unsorted: rows.length } }));
	await page.route(/\/api\/library\/facets$/, (route) => json(route, { media_types: { image: rows.length } }));
	await page.route(/\/api\/tags(\?.*)?$/, (route) => json(route, { tags: [] }));
}

test.describe('Kits in the Library', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await loginAsOwner(page);
	});

	test('the Library view opens from the sidebar and from ?view=kits, and lists kits with their counts', async ({ page }) => {
		await mockLibrary(page);
		await mockKits(page, [seedMara()]);
		await page.goto('/library?view=kits');
		await expect(page.getByRole('heading', { name: 'Kits' })).toBeVisible();
		await expect(page.locator('[data-kit-card]')).toHaveCount(1);
		await page.goto('/library');
		await page.getByRole('option', { name: /^Kits/ }).click();
		await expect(page).toHaveURL(/view=kits/);
		await screenshot(page, JOURNEY, 'list-1440');
	});

	test('creates a kit, renames an item in place and sees the refused clash with a suggestion', async ({ page }) => {
		await mockLibrary(page);
		const backend = await mockKits(page);
		await page.goto('/library?view=kits');
		await page.getByRole('button', { name: 'New kit' }).first().click();
		await page.getByLabel('Name', { exact: true }).fill('Old harbour');
		await expect(page.getByText('@kits.old_harbour')).toBeVisible();
		await page.getByRole('button', { name: 'Create' }).click();
		await expect(page.getByLabel('Kit name')).toHaveValue('Old harbour');
		expect(backend.kits).toHaveLength(1);

		await page.getByRole('button', { name: 'Add item' }).click();
		await page.getByRole('menuitem', { name: 'Write text' }).click();
		await page.getByLabel('Text', { exact: true }).fill('Low sun, wet stones.');
		await page.getByRole('button', { name: 'Add text' }).click();
		const tile = page.locator('[data-kit-item]').first();
		await expect(tile).toContainText('Low sun');

		await page.getByRole('button', { name: 'Add item' }).click();
		await page.getByRole('menuitem', { name: 'Upload file' }).click();
		await page.locator('input[type="file"]').first().setInputFiles({ name: 'a.png', mimeType: 'image/png', buffer: PNG });
		await expect(page.locator('[data-kit-item]')).toHaveCount(2);

		const name = page.locator('[data-kit-item]').nth(1).getByLabel('Item name');
		await name.fill('Profile Left');
		await expect(page.getByText('@kits.old_harbour.profile_left')).toBeVisible();
		await name.press('Enter');
		await expect(page.locator('[data-kit-item-name="profile_left"]')).toBeVisible();

		await page.locator('[data-kit-item]').first().getByLabel('Item name').fill('profile_left');
		await page.locator('[data-kit-item]').first().getByLabel('Item name').press('Enter');
		await expect(page.getByRole('alert').filter({ hasText: 'Already used' })).toContainText('profile_left2');
		await screenshot(page, JOURNEY, 'detail-1440');
	});

	test('adds Library items to a new kit from a selection', async ({ page }) => {
		await mockLibrary(page);
		const backend = await mockKits(page);
		await page.goto('/library');
		await page.locator('[data-library-card]').first().hover();
		await page.getByRole('button', { name: /Select/ }).first().click();
		await page.locator('[data-add-to-kit]').click();
		await expect(page.getByRole('dialog')).toContainText('to a kit');
		await page.getByLabel('Name', { exact: true }).fill('Dunes');
		await page.getByRole('button', { name: 'Create kit' }).click();
		await expect.poll(() => backend.kits.length).toBe(1);
		await expect(page).toHaveURL(/view=kits&kit=/);
	});

	for (const view of [
		{ name: 'All uploads', url: '/library' },
		{ name: 'the Kits list', url: '/library?view=kits' },
		{ name: 'an open kit', url: '/library?view=kits&kit=kt_7f3a1c2e' }
	]) {
		for (const size of [
			{ width: 1440, height: 900 },
			{ width: 390, height: 844 }
		]) {
			test(`${view.name} scrolls inside the Library area at ${size.width}px and leaves the page and header in place`, async ({ page }) => {
				await page.setViewportSize(size);
				await mockLibrary(page, 120);
				const mara = seedMara();
				const filler = Array.from({ length: 30 }, (_, index) => ({ ...mara, id: `kt_filler${index}`, name: `Filler ${index}`, handle: `filler_${index}` }));
				await mockKits(page, [mara, ...filler]);
				await page.goto(view.url);
				const area = page.locator('[data-library-scroll]');
				await expect(area).toBeVisible();
				if (view.url === '/library') await expect(page.locator('[data-library-card-body]').first()).toBeVisible();
				if (view.url.includes('view=kits') && !view.url.includes('kit=')) await expect(page.locator('[data-kit-card]').first()).toBeVisible();
				if (view.url.includes('kit=')) await expect(page.getByRole('button', { name: 'Delete kit' })).toBeVisible();
				const header = area.locator('.sticky, header').first();
				const frame = async () => ({
					page: await page.evaluate(() => document.scrollingElement!.scrollTop),
					area: await area.evaluate((node) => node.scrollTop),
					areaBox: await area.evaluate((node) => ({ top: node.getBoundingClientRect().top, height: node.getBoundingClientRect().height })),
					headerTop: await header.evaluate((node) => Math.round(node.getBoundingClientRect().top)),
					scrollable: await area.evaluate((node) => node.scrollHeight - node.clientHeight)
				});
				const before = await frame();
				const areaTop = before.areaBox.top;
				expect(before.areaBox.height).toBeLessThanOrEqual(size.height);
				if (before.scrollable > 0) {
					await page.mouse.move(size.width / 2, size.height / 2);
					await page.mouse.wheel(0, 1200);
					await expect.poll(async () => (await frame()).area).toBeGreaterThan(0);
				}
				const after = await frame();
				expect(after.page).toBe(0);
				expect(after.areaBox.top).toBe(areaTop);
				expect(after.headerTop).toBe(areaTop);
				if (size.width > 700 && !view.url.includes('kit=')) expect(before.scrollable).toBeGreaterThan(0);
			});
		}
	}

	test('deleting a kit asks first and keeps it when cancelled', async ({ page }) => {
		await mockLibrary(page);
		const backend = await mockKits(page, [seedMara()]);
		await page.goto('/library?view=kits&kit=kt_7f3a1c2e');
		await page.getByRole('button', { name: 'Delete kit' }).click();
		await expect(page.getByRole('alertdialog')).toBeVisible();
		await page.keyboard.press('Escape');
		expect(backend.kits).toHaveLength(1);
		await page.getByRole('button', { name: 'Delete kit' }).click();
		await page.getByRole('alertdialog').getByRole('button', { name: 'Delete kit' }).click();
		await expect.poll(() => backend.kits.length).toBe(0);
		await expect(page).toHaveURL(/view=kits$/);
	});
});

function historyFile(id: number, name: string) {
	return { id, file_path: `out/${name}`, file_type: 'image', is_final: true, width: 96, height: 96, created_at: NOW, thumbnail_small: PNG_URL, thumbnail_medium: PNG_URL, thumbnail_large: PNG_URL };
}

const HISTORY = [
	{ id: 'gen-a', preset_name: 'Portrait', status: 'completed', progress: 1, created_at: NOW, updated_at: NOW, form_data: { prompt: 'a woman in the rain' }, files: [historyFile(501, 'rain.png')], rating: 0, is_favorite: false, tags: [] },
	{ id: 'gen-b', preset_name: 'Portrait', status: 'completed', progress: 1, created_at: NOW, updated_at: NOW, form_data: { prompt: 'a woman at dawn' }, files: [historyFile(502, 'dawn.png')], rating: 0, is_favorite: false, tags: [] }
];

async function mockHistory(page: Page) {
	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) => json(route, { generations: HISTORY, total: HISTORY.length }));
	await page.route(/\/api\/media\/files\/\d+(\?.*)?$/, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: PNG }));
	await page.route(/\/api\/media\/generations\/[^/]+\/.+$/, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: PNG }));
}

test.describe('Kits and History', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await loginAsOwner(page);
	});

	test('adds items to a kit from the History picker, beside Library, Upload and Text', async ({ page }) => {
		await mockLibrary(page);
		await mockHistory(page);
		const backend = await mockKits(page, [seedMara()]);
		await page.goto('/library?view=kits&kit=kt_7f3a1c2e');
		for (const name of ['Library', 'History', 'Upload', 'Text']) await expect(page.getByRole('button', { name, exact: true })).toBeVisible();
		await page.getByRole('button', { name: 'History', exact: true }).click();
		await expect(page.getByText('Add to Mara from Generation History')).toBeVisible();
		await page.locator('[data-generation-card]').first().click();
		await page.locator('[data-generation-card]').nth(1).click();
		await page.getByRole('button', { name: 'Use selected' }).click();
		await expect.poll(() => backend.submitted.length).toBe(1);
		expect(backend.submitted[0].file_ids).toEqual(['501', '502']);
		expect(backend.kits[0].items).toHaveLength(5);
	});

	test('adds a History selection to a new kit with Add to kit', async ({ page }) => {
		await mockHistory(page);
		const backend = await mockKits(page);
		await page.goto('/history');
		await page.locator('[data-generation-card]').first().hover();
		await page.getByRole('button', { name: 'Select generation' }).first().click();
		await page.locator('[data-add-to-kit]').click();
		await expect(page.getByRole('dialog')).toContainText('to a kit');
		await page.getByLabel('Name', { exact: true }).fill('Rain');
		await page.getByRole('button', { name: 'Create kit' }).click();
		await expect.poll(() => backend.kits.length).toBe(1);
		expect(backend.submitted[0].file_ids).toEqual(['501']);
	});

	test('adds the open generation to a kit from its details', async ({ page }) => {
		await mockHistory(page);
		const backend = await mockKits(page, [seedMara()]);
		await page.goto('/history?generation=gen-a');
		await page.getByRole('button', { name: 'Add to kit' }).click();
		await page.getByRole('button', { name: 'Existing kit' }).click();
		await page.getByRole('button', { name: /Mara/ }).first().click();
		await page.getByRole('button', { name: 'Add to kit' }).last().click();
		await expect.poll(() => backend.submitted.length).toBe(1);
		expect(backend.submitted[0]).toMatchObject({ kit_id: 'kt_7f3a1c2e', file_ids: ['501'] });
	});
});

async function openReferencePreset(page: Page): Promise<boolean> {
	const token = await ownerToken(page);
	const headers = { Authorization: `Bearer ${token}` };
	const list = await page.request.get('/api/presets?include_uninstalled=true', { headers });
	const presets = ((await list.json()).data || []) as Array<{ id: string; name: string; installed?: boolean }>;
	const preset = presets.find((entry) => /minimax.*h3/i.test(entry.name));
	if (!preset) return false;
	if (!preset.installed) await page.request.post(`/api/presets/${preset.id}/install`, { headers });
	const me = await page.request.get('/api/auth/me', { headers });
	const userId = (await me.json()).data.id as string;
	await page.request.post(`/api/presets/${preset.id}/assign`, { headers, data: { user_ids: [userId] } });
	await page.goto('/generate');
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByRole('option', { name: new RegExp(preset.name) }).first().click();
	await page.getByTestId('preset-header-mode').getByRole('button').first().click();
	await page.getByRole('option', { name: /^References/ }).first().click();
	return true;
}

test.describe('Kits in a generation', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await loginAsOwner(page);
	});

	test('loads a kit into the reference loader, cites it with @kits., generates and reads the skipped notice', async ({ page }) => {
		const backend = await mockKits(page, [seedMara()]);
		test.skip(!(await openReferencePreset(page)), 'a MiniMax H3 preset with a References mode is not available on this instance');
		await page.route(/\/api\/generations\/start$/, async (route) => {
			backend.submitted.push(route.request().postDataJSON());
			backend.notices = [{ code: 'unsupported_kind', message: 'This model does not take audio: `voice` is skipped.', kit: 'mara', item: 'voice', kind: 'audio', field: 'references' }];
			await json(route, { generation_id: 'gen-kit-1', status: { status: 'queued' }, queue_position: null, kits: { used: [], notices: backend.notices } });
		});

		await page.getByRole('button', { name: 'Kits' }).first().click();
		const dialog = page.getByRole('dialog', { name: 'Load a kit' });
		await expect(dialog.locator('[data-kit-load-list] [role="option"]')).toHaveCount(1);
		await dialog.locator('[data-kit-load-list] [role="option"]').first().click();
		await dialog.getByRole('button', { name: /^Load Mara/ }).click();
		await expect(page.locator('[data-loaded-kit]')).toContainText('Mara');
		await screenshot(page, JOURNEY, 'loader-1440');

		const editor = page.locator('.segment-composer [contenteditable="true"]').first();
		await editor.click();
		await page.keyboard.type('@kits.');
		await expect(page.locator('.resource-picker')).toContainText('Mara');
		await page.keyboard.press('Enter');
		await expect(page.locator('.resource-picker')).toContainText('Everything this model can take');
		await expect(page.locator('[data-kit-chip]')).toHaveCount(0);
		await page.keyboard.press('Enter');
		await expect(page.locator('[data-kit-chip="ok"]')).toBeVisible();
		await screenshot(page, JOURNEY, 'picker-1440');

		await page.getByRole('button', { name: /^Generate/ }).first().click();
		await expect.poll(() => backend.submitted.length).toBe(1);
		await expect(page.getByText('voice is skipped')).toBeVisible();
	});

	test('the @ picker is driven by the keyboard: Kits group, a kit, an item, back with the left arrow', async ({ page }) => {
		await mockKits(page, [seedMara()]);
		test.skip(!(await openReferencePreset(page)), 'a MiniMax H3 preset with a References mode is not available on this instance');
		await page.getByRole('button', { name: 'Kits' }).first().click();
		const dialog = page.getByRole('dialog', { name: 'Load a kit' });
		await dialog.locator('[data-kit-load-list] [role="option"]').first().click();
		await dialog.getByRole('button', { name: /^Load Mara/ }).click();
		await expect(page.locator('[data-loaded-kit]')).toContainText('Mara');

		const editor = page.locator('.segment-composer [contenteditable="true"]').first();
		await editor.click();
		await page.keyboard.type('@kits');
		const picker = page.locator('.resource-picker');
		await expect(picker.locator('.picker-row[aria-selected="true"]')).toContainText('Kits');
		await page.keyboard.press('Enter');
		await expect(picker).toContainText('Mara');
		await page.keyboard.press('Enter');
		await expect(picker).toContainText('Everything this model can take');
		await page.keyboard.press('ArrowLeft');
		await expect(picker).not.toContainText('Everything this model can take');
		await page.keyboard.press('ArrowRight');
		await page.keyboard.press('ArrowDown');
		await expect(picker.locator('.picker-row[aria-selected="true"]')).toContainText('face');
		await page.keyboard.press('Enter');
		await expect(page.locator('[data-kit-chip="ok"]')).toContainText('mara.face');
	});

	test('kit rows written from outside the form survive a form re-hydrate', async ({ page }) => {
		await mockKits(page, [seedMara()]);
		test.skip(!(await openReferencePreset(page)), 'a MiniMax H3 preset with a References mode is not available on this instance');
		await page.getByRole('button', { name: 'Kits' }).first().click();
		const dialog = page.getByRole('dialog', { name: 'Load a kit' });
		await dialog.locator('[data-kit-load-list] [role="option"]').first().click();
		await dialog.getByRole('button', { name: /^Load Mara/ }).click();
		await expect(page.locator('[data-loaded-kit]')).toBeVisible();
		await page.getByTestId('preset-header-mode').getByRole('button').first().click();
		await page.getByRole('option').nth(0).click();
		await page.getByTestId('preset-header-mode').getByRole('button').first().click();
		await page.getByRole('option', { name: /^References/ }).first().click();
		await expect(page.locator('[data-loaded-kit]')).toContainText('Mara');
		await page.reload();
		await expect(page.locator('[data-loaded-kit]')).toContainText('Mara');
	});

	test('renaming and deleting a kit that a prompt cites changes its chips and tells the user', async ({ page }) => {
		const backend = await mockKits(page, [seedMara()]);
		test.skip(!(await openReferencePreset(page)), 'a MiniMax H3 preset with a References mode is not available on this instance');
		await page.getByRole('button', { name: 'Kits' }).first().click();
		const dialog = page.getByRole('dialog', { name: 'Load a kit' });
		await dialog.locator('[data-kit-load-list] [role="option"]').first().click();
		await dialog.getByRole('button', { name: /^Load Mara/ }).click();
		const editor = page.locator('.segment-composer [contenteditable="true"]').first();
		await editor.click();
		await page.keyboard.type('@kits.mara.');
		await page.keyboard.press('ArrowDown');
		await page.keyboard.press('Enter');
		await expect(page.locator('[data-kit-chip="ok"]')).toContainText('mara.face');

		backend.kits[0].handle = 'mara_lind';
		backend.kits[0].items[0].name = 'portrait';
		await page.reload();
		await expect(page.locator('[data-kit-chip="ok"]')).toContainText('mara_lind.portrait');
		await expect(page.getByText('Mara changed since this session was saved')).toBeVisible();

		backend.kits = [];
		await page.reload();
		await expect(page.locator('[data-kit-chip="kit-deleted"]')).toBeVisible();
		await expect(page.getByText('no longer exists').first()).toBeVisible();
		await screenshot(page, JOURNEY, 'deleted-1440');
	});
});
