import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'media-picker-multi-select';

const VIEWPORTS = [
	{ name: '1440', width: 1440, height: 900 },
	{ name: '390', width: 390, height: 844 }
];

function swatch(color: string): string {
	const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><rect width="320" height="180" fill="${color}"/></svg>`;
	return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

function file(id: number, type: string, name: string, color: string) {
	return {
		id,
		file_path: `out/${name}`,
		file_type: type,
		is_final: true,
		created_at: '2026-01-01T00:00:00Z',
		thumbnail_small: swatch(color),
		thumbnail_medium: swatch(color),
		thumbnail_large: swatch(color)
	};
}

const GENERATIONS = [
	{
		id: 'gen-1',
		preset_name: 'Landscape',
		status: 'completed',
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		form_data: {},
		files: [file(1, 'image', 'dunes-01.png', '#6b7a8f'), file(2, 'image', 'dunes-02.png', '#8f7a6b')],
		rating: 0,
		is_favorite: false
	},
	{
		id: 'gen-2',
		preset_name: 'Portrait',
		status: 'completed',
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		form_data: {},
		files: [file(3, 'video', 'orbit.mp4', '#4f6f5f')],
		rating: 0,
		is_favorite: false
	}
];

const LIBRARY = [
	{ id: 'lib-1', filename: 'a', original_filename: 'harbour.png', media_type: 'image', url: swatch('#5f6f8f'), thumbnail_medium: swatch('#5f6f8f'), width: 1024, height: 576, tags: [] },
	{ id: 'lib-2', filename: 'b', original_filename: 'market.png', media_type: 'image', url: swatch('#8f6f5f'), thumbnail_medium: swatch('#8f6f5f'), width: 1024, height: 576, tags: [] },
	{ id: 'lib-3', filename: 'c', original_filename: 'walkthrough.mp4', media_type: 'video', url: '', thumbnail_medium: swatch('#4f6f5f'), tags: [] }
];

async function json(route: Route, data: unknown) {
	await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

async function mockMedia(page: Page) {
	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) =>
		json(route, { generations: GENERATIONS, total: GENERATIONS.length })
	);
	await page.route(/\/api\/library\/items(\?.*)?$/, (route) =>
		json(route, { items: LIBRARY, total: LIBRARY.length, limit: 20, offset: 0 })
	);
	await page.route(/\/api\/tags(\?.*)?$/, (route) => json(route, { tags: [] }));
}

async function openPicker(page: Page, name: string, props: Record<string, unknown>) {
	await page.evaluate(
		({ name, props }) => {
			const host = (window as any).__potionui;
			const entry = host.components[name];
			const el = document.body.appendChild(document.createElement('div'));
			(window as any).__pickerEvents = { selection: [], confirmed: null };
			const handle = entry.mount(el, {
				isOpen: true,
				multiple: true,
				...props,
				onSelectionChange: (keys: string[]) => ((window as any).__pickerEvents.selection = keys),
				onConfirm: (keys: string[], items: unknown[]) => ((window as any).__pickerEvents.confirmed = { keys, items }),
				onClose: () => {
					entry.unmount(handle);
					el.remove();
				}
			});
		},
		{ name, props }
	);
}

for (const viewport of VIEWPORTS) {
	test(`history picker multi-select at ${viewport.name}`, async ({ page }) => {
		await page.setViewportSize({ width: viewport.width, height: viewport.height });
		await mockMedia(page);
		await loginAsOwner(page);
		await page.waitForFunction(() => !!(window as any).__potionui?.components?.GenerationHistoryModal);

		await openPicker(page, 'GenerationHistoryModal', { selectableMediaTypes: ['image'] });
		const dialog = page.getByRole('dialog');
		await expect(dialog.getByLabel('Select dunes-01.png')).toBeVisible({ timeout: 20000 });

		await dialog.getByLabel('Select dunes-01.png').check();
		await dialog.getByLabel('Select dunes-02.png').check();
		await expect(dialog.getByTestId('picker-selected-count')).toHaveText('2 selected');
		await expect(dialog.getByLabel('Select orbit.mp4')).toBeDisabled();
		await expect(dialog.getByText('Only image files can be selected here').first()).toBeVisible();

		await screenshot(page, JOURNEY, `history-${viewport.name}`);

		await dialog.getByRole('button', { name: 'Use selected' }).click();
		const confirmed = await page.evaluate(() => (window as any).__pickerEvents.confirmed);
		expect(confirmed.keys).toEqual(['gen-1:1', 'gen-1:2']);
	});

	test(`library picker multi-select at ${viewport.name}`, async ({ page }) => {
		await page.setViewportSize({ width: viewport.width, height: viewport.height });
		await mockMedia(page);
		await loginAsOwner(page);
		await page.waitForFunction(() => !!(window as any).__potionui?.components?.UploadLibraryModal);

		await openPicker(page, 'UploadLibraryModal', {});
		const dialog = page.getByRole('dialog');
		await expect(dialog.getByLabel('Select harbour.png')).toBeVisible({ timeout: 20000 });

		await dialog.getByLabel('Select harbour.png').check();
		await dialog.getByLabel('Select walkthrough.mp4').check();
		await expect(dialog.getByTestId('picker-selected-count')).toHaveText('2 selected');
		await dialog.getByRole('button', { name: 'Preview market.png' }).click();
		await expect(dialog.getByTestId('picker-preview')).toBeVisible();
		await expect(dialog.getByTestId('picker-selected-count')).toHaveText('2 selected');

		await screenshot(page, JOURNEY, `library-${viewport.name}`);

		await dialog.getByRole('button', { name: 'Cancel' }).first().click();
		await expect(dialog).toHaveCount(0);
		const selection = await page.evaluate(() => (window as any).__pickerEvents.selection);
		expect(selection).toEqual([]);
	});
}
