import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'upload-picker-mosaic';

const VIEWPORTS = [
	{ name: '1440', width: 1440, height: 900 },
	{ name: '390', width: 390, height: 844 }
];

function swatch(color: string, width: number, height: number): string {
	const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="${width}" height="${height}" fill="${color}"/><circle cx="${width / 2}" cy="${height / 2}" r="${Math.min(width, height) / 4}" fill="#ffffff" fill-opacity="0.25"/></svg>`;
	return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

function image(id: string, name: string, color: string, width: number, height: number) {
	return {
		id,
		filename: `stored-${id}`,
		original_filename: name,
		media_type: 'image',
		url: swatch(color, width, height),
		thumbnail_medium: swatch(color, Math.round(width / 4), Math.round(height / 4)),
		width,
		height,
		size: width * height,
		created_at: '2026-01-01T00:00:00Z',
		tags: []
	};
}

const LIBRARY = [
	image('lib-portrait', 'lighthouse.png', '#6b7a8f', 832, 1216),
	image('lib-square', 'teapot.png', '#8f7a6b', 1024, 1024),
	image('lib-landscape', 'salt-flats.png', '#5f8f7a', 1536, 864),
	image('lib-wide', 'harbour.png', '#7a5f8f', 1536, 864),
	{
		id: 'lib-video',
		filename: 'stored-video',
		original_filename: 'walkthrough.mp4',
		media_type: 'video',
		url: '',
		thumbnail_medium: swatch('#4f6f5f', 320, 180),
		width: 1280,
		height: 720,
		duration_seconds: 8.4,
		fps: 24,
		size: 3400000,
		created_at: '2026-01-01T00:00:00Z',
		tags: []
	},
	{
		id: 'lib-video-bare',
		filename: 'stored-video-bare',
		original_filename: 'clip-without-thumbnail.mp4',
		media_type: 'video',
		url: '',
		created_at: '2026-01-01T00:00:00Z',
		tags: []
	},
	{
		id: 'lib-audio',
		filename: 'stored-audio',
		original_filename: 'rain.wav',
		media_type: 'audio',
		url: '',
		duration_seconds: 12,
		size: 480000,
		created_at: '2026-01-01T00:00:00Z',
		tags: []
	}
];

async function json(route: Route, data: unknown) {
	await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

async function mockMedia(page: Page) {
	await page.route(/\/api\/library\/items(\?.*)?$/, (route) =>
		json(route, { items: LIBRARY, total: LIBRARY.length, limit: 20, offset: 0 })
	);
	await page.route(/\/api\/library\/items\/lib-square$/, (route) =>
		json(route, { id: 'lib-square', deleted: true })
	);
	await page.route(/\/api\/tags(\?.*)?$/, (route) => json(route, { tags: [] }));
}

async function openPicker(page: Page, props: Record<string, unknown>) {
	await page.evaluate((props) => {
		const entry = (window as any).__potionui.components.UploadLibraryModal;
		const el = document.body.appendChild(document.createElement('div'));
		(window as any).__pickerEvents = { selected: null, confirmed: null };
		const handle = entry.mount(el, {
			isOpen: true,
			...props,
			onSelect: (item: { id: string }) => ((window as any).__pickerEvents.selected = item.id),
			onConfirm: (keys: string[]) => ((window as any).__pickerEvents.confirmed = keys),
			onClose: () => {
				entry.unmount(handle);
				el.remove();
			}
		});
	}, props);
}

async function box(page: Page, selector: string) {
	const found = await page.locator(selector).boundingBox();
	if (!found) throw new Error(`no box for ${selector}`);
	return found;
}

async function frameBox(page: Page, itemId: string) {
	return box(page, `[data-item-id="${itemId}"] [style*="aspect-ratio"]`);
}

for (const viewport of VIEWPORTS) {
	test(`upload picker mosaic single-pick at ${viewport.name}`, async ({ page }) => {
		await page.setViewportSize({ width: viewport.width, height: viewport.height });
		await mockMedia(page);
		await loginAsOwner(page);
		await page.waitForFunction(() => !!(window as any).__potionui?.components?.UploadLibraryModal);

		await openPicker(page, { multiple: false });
		const dialog = page.getByRole('dialog');
		await expect(dialog.locator('[data-item-id="lib-portrait"]')).toBeVisible({ timeout: 20000 });
		await expect(dialog.locator('[data-testid="library-file-tile"]')).toHaveCount(7);

		const portrait = await frameBox(page, 'lib-portrait');
		const square = await frameBox(page, 'lib-square');
		const landscape = await frameBox(page, 'lib-landscape');
		const video = await frameBox(page, 'lib-video');
		const audio = await frameBox(page, 'lib-audio');
		expect(portrait.width / portrait.height).toBeCloseTo(832 / 1216, 1);
		expect(square.width / square.height).toBeCloseTo(1, 1);
		expect(landscape.width / landscape.height).toBeCloseTo(1536 / 864, 1);
		expect(video.width / video.height).toBeCloseTo(1280 / 720, 1);
		expect(audio.width / audio.height).toBeCloseTo(16 / 9, 1);

		await screenshot(page, JOURNEY, `single-${viewport.name}`);

		const tileButton = dialog.getByRole('button', { name: 'Select salt-flats.png' });
		await tileButton.click();
		expect(await page.evaluate(() => (window as any).__pickerEvents.selected)).toBe('lib-landscape');
	});

	test(`upload picker offers no delete control on a tile at ${viewport.name}`, async ({ page }) => {
		await page.setViewportSize({ width: viewport.width, height: viewport.height });
		await mockMedia(page);
		await loginAsOwner(page);
		await page.waitForFunction(() => !!(window as any).__potionui?.components?.UploadLibraryModal);

		await openPicker(page, { multiple: false });
		const dialog = page.getByRole('dialog');
		await expect(dialog.locator('[data-item-id="lib-square"]')).toBeVisible({ timeout: 20000 });
		await expect(dialog.getByRole('button', { name: /^Delete / })).toHaveCount(0);
		expect(await page.evaluate(() => (window as any).__pickerEvents.selected)).toBeNull();
	});

	test(`upload picker mosaic multi-select at ${viewport.name}`, async ({ page }) => {
		await page.setViewportSize({ width: viewport.width, height: viewport.height });
		await mockMedia(page);
		await loginAsOwner(page);
		await page.waitForFunction(() => !!(window as any).__potionui?.components?.UploadLibraryModal);

		await openPicker(page, { multiple: true, selectableMediaTypes: ['image', 'video'] });
		const dialog = page.getByRole('dialog');
		await expect(dialog.locator('[data-item-id="lib-portrait"]')).toBeVisible({ timeout: 20000 });
		await expect(dialog.locator('[data-testid="library-file-tile"]')).toHaveCount(7);

		await dialog.getByRole('checkbox', { name: 'Select lighthouse.png' }).check();
		await dialog.getByRole('checkbox', { name: 'Select salt-flats.png' }).check();
		await expect(dialog.getByTestId('picker-selected-count')).toHaveText('2 selected');
		await expect(dialog.locator('[data-item-id="lib-portrait"]')).toHaveAttribute('data-selected', 'true');
		await expect(dialog.getByRole('checkbox', { name: 'Select rain.wav' })).toBeDisabled();
		await expect(dialog.getByText('Only image and video files can be selected here').first()).toBeVisible();

		await dialog.locator('[data-item-id="lib-square"] button[aria-hidden="true"]').click();
		await expect(dialog.getByTestId('picker-selected-count')).toHaveText('3 selected');
		await dialog.locator('[data-item-id="lib-square"] button[aria-hidden="true"]').click();
		await expect(dialog.getByTestId('picker-selected-count')).toHaveText('2 selected');
		await expect(dialog.getByRole('button', { name: /^Preview/ })).toHaveCount(0);

		await screenshot(page, JOURNEY, `multi-${viewport.name}`);

		await dialog.locator('[data-item-id="lib-audio"]').scrollIntoViewIfNeeded();
		await page.waitForTimeout(400);
		await expect(dialog.locator('[data-item-id="lib-audio"]').getByText('Only image and video files can be selected here')).toBeVisible();
		await screenshot(page, JOURNEY, `multi-audio-${viewport.name}`);

		await dialog.getByRole('button', { name: 'Use selected' }).click();
		const confirmed = await page.evaluate(() => (window as any).__pickerEvents.confirmed);
		expect(confirmed).toEqual(['lib-portrait', 'lib-landscape']);
	});
}

test('the Library wrapper keeps its one source: no tab strip, and the shared tile size control persists', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await mockMedia(page);
	await loginAsOwner(page);
	await page.waitForFunction(() => !!(window as any).__potionui?.components?.UploadLibraryModal);

	await openPicker(page, { multiple: false });
	const dialog = page.getByRole('dialog');
	await expect(dialog.locator('[data-item-id="lib-portrait"]')).toBeVisible({ timeout: 20000 });
	await expect(dialog.getByRole('navigation', { name: 'Source' })).toHaveCount(0);
	await dialog.getByRole('radio', { name: 'Large tiles' }).click();
	await expect.poll(() => page.evaluate(() => window.localStorage.getItem('history-tile-size'))).toBe('large');
	await expect(dialog.getByRole('radio', { name: 'Large tiles' })).toHaveAttribute('aria-checked', 'true');
});

