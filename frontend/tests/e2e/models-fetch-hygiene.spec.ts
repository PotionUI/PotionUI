import { test, expect, type Page } from '@playwright/test';
import { fulfillJson, loginAsOwner } from './helpers';

interface Counts {
	models: URL[];
	types: URL[];
}

function model(id: string) {
	return {
		id,
		filename: `${id}.safetensors`,
		model_type: 'checkpoint',
		tags: [],
		file_path: `/models/${id}.safetensors`
	};
}

async function serveModels(page: Page, answer: (url: URL) => Promise<unknown> | unknown): Promise<Counts> {
	const counts: Counts = { models: [], types: [] };
	await page.route(
		(url) => url.pathname === '/api/models/types',
		(route) => {
			counts.types.push(new URL(route.request().url()));
			return fulfillJson(route, { success: true, data: { types: [], total: 0 } });
		}
	);
	await page.route(
		(url) => url.pathname === '/api/models',
		async (route) => {
			if (route.request().method() !== 'GET') return route.fallback();
			const url = new URL(route.request().url());
			counts.models.push(url);
			const models = await answer(url);
			return fulfillJson(route, {
				success: true,
				data: { models, total: (models as unknown[]).length, availability_indexed: true }
			});
		}
	);
	return counts;
}

test.describe('models page fetch hygiene', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await loginAsOwner(page);
	});

	test('entering the page requests the list once', async ({ page }) => {
		const counts = await serveModels(page, () => [model('alpha')]);
		await page.goto('/models');
		await expect(page.getByText('alpha.safetensors').first()).toBeVisible({ timeout: 15000 });
		await page.waitForTimeout(1200);
		expect(counts.models).toHaveLength(1);
	});

	test('typing a search issues one request after the pause and keeps the newest result', async ({ page }) => {
		const counts = await serveModels(page, async (url) => {
			const term = url.searchParams.get('search');
			if (term === 'f') await new Promise((resolve) => setTimeout(resolve, 600));
			return term ? [model(`hit-${term}`)] : [model('alpha')];
		});
		await page.goto('/models');
		await expect(page.getByText('alpha.safetensors').first()).toBeVisible({ timeout: 15000 });
		const before = counts.models.length;

		const input = page.getByLabel('Search models');
		await input.pressSequentially('flux', { delay: 40 });
		await expect(page.getByText('hit-flux.safetensors').first()).toBeVisible({ timeout: 10000 });
		await page.waitForTimeout(900);

		const searches = counts.models.slice(before).map((url) => url.searchParams.get('search'));
		expect(searches).toEqual(['flux']);
		await expect(page.getByText('hit-f.safetensors')).toHaveCount(0);
	});
});
