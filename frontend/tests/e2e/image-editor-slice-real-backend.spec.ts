import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { HEIGHT, WIDTH, sheetPng } from './sliceSheet';

const JOURNEY = 'image-editor-slice-real-backend';

interface LibraryRow {
	id: string;
	original_filename?: string;
	width?: number;
	height?: number;
	provenance?: { slice?: { source_item_id?: string; source_name?: string; index: number; count: number; rect: unknown } } | null;
}

async function libraryRows(page: Page, token: string): Promise<LibraryRow[]> {
	const response = await page.request.get('/api/library/items?limit=200&offset=0', { headers: { Authorization: `Bearer ${token}` } });
	expect(response.ok(), `library list -> ${response.status()}`).toBeTruthy();
	return ((await response.json()).data?.items ?? []) as LibraryRow[];
}

function canvasSamples(page: Page): Promise<{ width: number; height: number; samples: number[][] }> {
	return page.locator('[data-editor-layer]').evaluate((node) => {
		const canvas = node as HTMLCanvasElement;
		const context = canvas.getContext('2d');
		if (!context || canvas.width < 2 || canvas.height < 2) return { width: canvas.width, height: canvas.height, samples: [] };
		const points = [
			[0.15, 0.25],
			[0.5, 0.25],
			[0.85, 0.25],
			[0.3, 0.75],
			[0.5, 0.49]
		];
		return {
			width: canvas.width,
			height: canvas.height,
			samples: points.map(([fx, fy]) => Array.from(context.getImageData(Math.floor(canvas.width * fx), Math.floor(canvas.height * fy), 1, 1).data))
		};
	});
}

test.describe('Slice against the real backend', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('a real sheet sliced into references lands six Library items with provenance', async ({ page }) => {
		test.setTimeout(180000);
		const problems: string[] = [];
		page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`));
		page.on('response', (response) => {
			const path = new URL(response.url()).pathname;
			if (response.status() >= 500 && path.startsWith('/api/')) problems.push(`${response.request().method()} ${path} ${response.status()}`);
		});

		await loginAsOwner(page);
		const token = await ownerToken(page);
		const stem = `real-sheet-${Date.now()}`;
		const upload = await page.request.post('/api/media/upload', {
			headers: { Authorization: `Bearer ${token}` },
			multipart: { file: { name: `${stem}.png`, mimeType: 'image/png', buffer: sheetPng() } }
		});
		expect(upload.ok(), `upload -> ${upload.status()}`).toBeTruthy();
		const source = (await libraryRows(page, token)).find((row) => row.original_filename === `${stem}.png`);
		expect(source, 'the uploaded sheet is in the Library').toBeTruthy();

		await page.goto('/library');
		await page.locator('[data-library-card]').filter({ hasText: stem }).locator('.media-zoom').click();
		const bar = page.getByRole('toolbar', { name: 'Actions' });
		await expect(bar).toBeVisible();
		await bar.getByRole('button', { name: 'Edit' }).click();
		await page.getByRole('menu', { name: 'Edit' }).getByRole('menuitem', { name: 'Slice into references', exact: true }).click();

		await expect(page).toHaveURL(/\/generate/);
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'slice', { timeout: 15000 });
		await expect(page.locator('[data-render-error]')).toHaveCount(0);
		await expect
			.poll(
				async () => {
					const pixels = await canvasSamples(page);
					const shape = Math.abs(pixels.width / pixels.height - WIDTH / HEIGHT) < 0.02;
					return shape && pixels.samples.length === 5 && pixels.samples.every(([r, g, b, a]) => a > 200 && Math.max(r, g, b) > 40);
				},
				{ message: 'the sheet should be drawn, not a black canvas', timeout: 90000 }
			)
			.toBe(true);
		const cells = page.locator('[data-slice-overlay] [data-slice-cell]');
		await expect(cells).toHaveCount(6, { timeout: 15000 });

		await page.locator('[data-slice-detect]').first().click();
		await expect(page.locator('[data-slice-detected]')).toContainText('6 panels found');
		await expect(cells).toHaveCount(6);
		await screenshot(page, JOURNEY, 'detected-1440');

		const primary = page.locator('[data-frame-save]');
		await expect(primary).toHaveText('Save 6 images');
		await primary.click();
		await expect(page.locator('[role="status"]').filter({ hasText: 'Saved 6 images to Library' })).toBeVisible({ timeout: 60000 });
		await expect(primary).toHaveText('Saved');
		await screenshot(page, JOURNEY, 'saved-1440');

		const sliced = (await libraryRows(page, token)).filter((row) => row.provenance?.slice?.source_item_id === source!.id);
		expect(sliced).toHaveLength(6);
		expect(sliced.map((row) => row.provenance!.slice!.index).sort()).toEqual([0, 1, 2, 3, 4, 5]);
		for (const row of sliced) {
			expect(row.provenance!.slice!.count).toBe(6);
			expect(row.provenance!.slice!.source_name).toBe(`${stem}.png`);
			expect(row.original_filename).toMatch(new RegExp(`^${stem}-0[1-6]`));
		}
		const wide = sliced.find((row) => row.provenance!.slice!.index === 3)!;
		expect(wide.width).toBeGreaterThan(500);
		expect(problems).toEqual([]);

		await page.goto('/library');
		await page.locator('[data-library-card]').filter({ hasText: `${stem}-04` }).locator('.media-zoom').click();
		await expect(page.locator('[data-slice-origin]')).toHaveText(`From ${stem} · cell 4 of 6`);
		await screenshot(page, JOURNEY, 'provenance-1440');
	});
});
