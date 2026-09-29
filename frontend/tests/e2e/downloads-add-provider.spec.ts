import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner } from './helpers';

const CIVITAI_URL = 'https://civitai.com/api/download/models/3361846';

async function openDialog(page: Page, bodies: Array<Record<string, unknown>>) {
	await loginAsOwner(page);
	await page.route(/\/api\/providers$/, (route) =>
		route.fulfill({
			json: {
				success: true,
				data: [
					{ id: 'civitai', name: 'CivitAI' },
					{ id: 'huggingface', name: 'HuggingFace' }
				]
			}
		})
	);
	await page.route(/\/api\/downloads\/model$/, async (route) => {
		bodies.push(route.request().postDataJSON());
		await route.fulfill({
			json: {
				success: true,
				data: {
					id: 'dl-1',
					type: 'model',
					url: CIVITAI_URL,
					destination_path: '/models/m.safetensors',
					filename: 'm.safetensors',
					status: 'pending',
					progress: 0,
					provider_id: null,
					tags: [],
					retry_count: 0
				}
			}
		});
	});
	await page.setViewportSize({ width: 1440, height: 900 });
	await page.goto('/admin?tab=downloads');
	await page.getByRole('button', { name: /Add (Your First )?Download/i }).first().click();
	await page.locator('#url').fill(CIVITAI_URL);
	await page.getByRole('button', { name: 'Advanced' }).click();
	await expect(page.locator('#provider')).toBeVisible();
}

test('a pasted CivitAI link submits the detected provider without touching the selector', async ({ page }) => {
	const bodies: Array<Record<string, unknown>> = [];
	await openDialog(page, bodies);
	await expect(page.locator('#provider')).toHaveValue('civitai');
	await page.getByRole('button', { name: 'Queue download' }).click();
	await expect.poll(() => bodies.length).toBe(1);
	expect(bodies[0].provider_id).toBe('civitai');
});

test('choosing No provider sends none, and a manual provider is sent as picked', async ({ page }) => {
	const bodies: Array<Record<string, unknown>> = [];
	await openDialog(page, bodies);
	await page.locator('#provider').selectOption('none');
	await page.getByRole('button', { name: 'Queue download' }).click();
	await expect.poll(() => bodies.length).toBe(1);
	expect(bodies[0].provider_id).toBe('none');
});

test('a manual provider wins over the detected one', async ({ page }) => {
	const bodies: Array<Record<string, unknown>> = [];
	await openDialog(page, bodies);
	await page.locator('#provider').selectOption('huggingface');
	await page.getByRole('button', { name: 'Queue download' }).click();
	await expect.poll(() => bodies.length).toBe(1);
	expect(bodies[0].provider_id).toBe('huggingface');
});
