import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'history-picker-mosaic';

const VIEWPORTS = [
	{ name: '1440', width: 1440, height: 900 },
	{ name: '390', width: 390, height: 844 }
];

function swatch(color: string, width: number, height: number): string {
	const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="${width}" height="${height}" fill="${color}"/></svg>`;
	return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

function image(id: number, name: string, color: string, width: number, height: number) {
	const thumb = swatch(color, Math.round(width / 4), Math.round(height / 4));
	return {
		id,
		file_path: `out/${name}`,
		file_type: 'image',
		is_final: true,
		width,
		height,
		created_at: '2026-01-01T00:00:00Z',
		thumbnail_small: thumb,
		thumbnail_medium: thumb,
		thumbnail_large: thumb
	};
}

function generation(id: string, prompt: string, files: unknown[]) {
	return {
		id,
		preset_name: 'Mosaic',
		status: 'completed',
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		form_data: { prompt },
		files,
		rating: 0,
		is_favorite: false
	};
}

const GENERATIONS = [
	generation('gen-portrait', 'lighthouse in the fog', [image(1, 'lighthouse.png', '#6b7a8f', 832, 1216)]),
	generation('gen-square', 'ceramic teapot', [image(2, 'teapot.png', '#8f7a6b', 1024, 1024)]),
	generation('gen-landscape', 'salt flats at noon', [
		image(3, 'salt-01.png', '#5f8f7a', 1536, 864),
		image(4, 'salt-02.png', '#7a5f8f', 1536, 864)
	]),
	generation('gen-audio', 'rain on a tin roof', [
		{
			id: 5,
			file_path: 'out/rain.wav',
			file_type: 'audio',
			is_final: true,
			duration_seconds: 12,
			created_at: '2026-01-01T00:00:00Z'
		}
	])
];

async function json(route: Route, data: unknown) {
	await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

async function mockMedia(page: Page) {
	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) =>
		json(route, { generations: GENERATIONS, total: GENERATIONS.length })
	);
	await page.route(/\/api\/tags(\?.*)?$/, (route) => json(route, { tags: [] }));
	await page.route(/\/api\/media\/generations\/[^/]+\/.+$/, (route) => {
		const name = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop() ?? '');
		const found = GENERATIONS.flatMap((g) => g.files as ReturnType<typeof image>[]).find((f) => f.file_path.endsWith(name));
		if (!found || !found.width) return route.fulfill({ status: 404 });
		const svg = decodeURIComponent(found.thumbnail_large.split(',')[1]).replace(
			/width="\d+" height="\d+"/,
			`width="${found.width}" height="${found.height}"`
		);
		return route.fulfill({ status: 200, contentType: 'image/svg+xml', body: svg });
	});
}

async function openPicker(page: Page, props: Record<string, unknown>) {
	await page.evaluate((props) => {
		const entry = (window as any).__potionui.components.GenerationHistoryModal;
		const el = document.body.appendChild(document.createElement('div'));
		(window as any).__pickerEvents = { selected: null, confirmed: null };
		const handle = entry.mount(el, {
			isOpen: true,
			...props,
			onSelect: (generation: { id: string }, file: { id: number }) =>
				((window as any).__pickerEvents.selected = { generationId: generation.id, fileId: file.id }),
			onConfirm: (keys: string[]) => ((window as any).__pickerEvents.confirmed = keys),
			onClose: () => {
				entry.unmount(handle);
				el.remove();
			}
		});
	}, props);
}

async function frameSize(page: Page, fileId: number) {
	const box = await page.locator(`[data-file-id="${fileId}"] [style*="aspect-ratio"]`).boundingBox();
	if (!box) throw new Error(`no frame for file ${fileId}`);
	return box;
}

for (const viewport of VIEWPORTS) {
	test(`history picker mosaic single-pick at ${viewport.name}`, async ({ page }) => {
		await page.setViewportSize({ width: viewport.width, height: viewport.height });
		await mockMedia(page);
		await loginAsOwner(page);
		await page.waitForFunction(() => !!(window as any).__potionui?.components?.GenerationHistoryModal);

		await openPicker(page, { multiple: false });
		const dialog = page.getByRole('dialog');
		await expect(dialog.locator('[data-file-id="1"]')).toBeVisible({ timeout: 20000 });
		await expect(dialog.locator('[data-testid="history-file-tile"]')).toHaveCount(5);

		const portrait = await frameSize(page, 1);
		const square = await frameSize(page, 2);
		const landscape = await frameSize(page, 3);
		expect(portrait.width / portrait.height).toBeCloseTo(832 / 1216, 1);
		expect(square.width / square.height).toBeCloseTo(1, 1);
		expect(landscape.width / landscape.height).toBeCloseTo(1536 / 864, 1);

		await screenshot(page, JOURNEY, `single-${viewport.name}`);

		const tileButton = dialog.getByRole('button', { name: 'Select salt-02.png (salt flats at noon)' });
		await tileButton.click();
		const selected = await page.evaluate(() => (window as any).__pickerEvents.selected);
		expect(selected).toEqual({ generationId: 'gen-landscape', fileId: 4 });
	});

	test(`history picker mosaic multi-pick at ${viewport.name}`, async ({ page }) => {
		await page.setViewportSize({ width: viewport.width, height: viewport.height });
		await mockMedia(page);
		await loginAsOwner(page);
		await page.waitForFunction(() => !!(window as any).__potionui?.components?.GenerationHistoryModal);

		await openPicker(page, { multiple: true, selectableMediaTypes: ['image'] });
		const dialog = page.getByRole('dialog');
		await expect(dialog.locator('[data-file-id="1"]')).toBeVisible({ timeout: 20000 });

		await dialog.getByRole('checkbox', { name: 'Select lighthouse.png (lighthouse in the fog)' }).check();
		await dialog.getByRole('checkbox', { name: 'Select salt-01.png (salt flats at noon)' }).check();
		await expect(dialog.getByTestId('picker-selected-count')).toHaveText('2 selected');
		await expect(dialog.getByRole('checkbox', { name: 'Select rain.wav (rain on a tin roof)' })).toBeDisabled();
		await expect(dialog.getByText('Only image files can be selected here').first()).toBeVisible();

		await dialog.locator('[data-file-id="2"] button[aria-hidden="true"]').click();
		await expect(dialog.getByTestId('picker-selected-count')).toHaveText('3 selected');
		await dialog.locator('[data-file-id="2"] button[aria-hidden="true"]').click();
		await expect(dialog.getByTestId('picker-selected-count')).toHaveText('2 selected');
		await expect(dialog.getByRole('button', { name: /^Preview/ })).toHaveCount(0);

		await screenshot(page, JOURNEY, `multi-${viewport.name}`);

		await dialog.locator('[data-file-id="5"]').scrollIntoViewIfNeeded();
		await expect(dialog.locator('[data-file-id="5"]').getByText('Only image files can be selected here')).toBeVisible();
		await screenshot(page, JOURNEY, `multi-audio-${viewport.name}`);

		await dialog.getByRole('button', { name: 'Use selected' }).click();
		const confirmed = await page.evaluate(() => (window as any).__pickerEvents.confirmed);
		expect(confirmed).toEqual(['gen-portrait:1', 'gen-landscape:3']);
	});
}
