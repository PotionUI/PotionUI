import { expect, test } from '@playwright/test';
import { historyRecords, thumbnailPng } from './fixtures';
import { runScenario } from './perfKit';

const COUNT = 96;
const MOVES = 120;
const GRID = '[role="listbox"][aria-label="Generation history"]';
const CARD = '[data-history-card]';

test('history-marquee', async ({ browser }) => {
	const records = historyRecords(COUNT);
	const thumbnail = thumbnailPng();
	await runScenario<null>(browser, {
		name: 'history-marquee',
		viewport: { width: 1440, height: 10000 },
		configure: async (context) => {
			await context.route(/\/api\/media\/|\/outputs\//, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: thumbnail }));
			await context.route(/\/api\/generations\/history(\?.*)?$/, (route) =>
				route.request().method() === 'GET'
					? route.fulfill({ json: { success: true, data: { generations: records, total: records.length, limit: records.length, offset: 0 } } })
					: route.fallback()
			);
		},
		prepare: async (kit) => {
			await kit.page.goto('/history');
			await expect(kit.page.locator(CARD)).toHaveCount(COUNT, { timeout: 30000 });
			await kit.page.waitForLoadState('networkidle');
			await kit.page.waitForTimeout(1000);
		},
		measure: async (kit) => {
			const grid = (await kit.page.locator(GRID).boundingBox())!;
			const startX = grid.x + grid.width - 6;
			const startY = grid.y + grid.height - 6;
			const endX = grid.x + 2;
			const endY = grid.y + 2;
			await kit.page.mouse.move(startX, startY);
			const { metrics } = await kit.window(async () => {
				await kit.page.mouse.down();
				for (let i = 1; i <= MOVES; i++) {
					await kit.page.mouse.move(startX + ((endX - startX) * i) / MOVES, startY + ((endY - startY) * i) / MOVES);
				}
				await kit.page.mouse.up();
			});
			const selected = await kit.page.locator(`${CARD}[aria-selected="true"]`).count();
			if (selected !== COUNT) throw new Error(`marquee selected ${selected} of ${COUNT} cards; grid box ${JSON.stringify(grid)}`);
			return { ...metrics, selected_count: selected };
		}
	});
});
