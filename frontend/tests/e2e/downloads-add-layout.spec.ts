import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner } from './helpers';

test.afterEach(async ({ page }) => {
	await page.unrouteAll({ behavior: 'ignoreErrors' });
});

const SHOTS = process.env.ADD_DOWNLOAD_SHOTS;

const TYPES = [
	{ type: 'controlnet', directory: '/srv/potionui/models/controlnet', count: 0, subdirectories: [] },
	{ type: 'lora', directory: '/srv/potionui/models/loras', count: 831, subdirectories: ['sdxl', 'sdxl/characters'] },
	{ type: 'checkpoint', directory: '/srv/potionui/models/checkpoints', count: 12, subdirectories: ['sdxl', 'flux'] },
	{ type: 'diffusion_model', directory: '/srv/potionui/models/diffusion_models', count: 71, subdirectories: [] },
	{ type: 'vae', directory: '/srv/potionui/models/vae', count: 0, subdirectories: [] }
];

async function openDialog(page: Page, remote: boolean, width: number, height: number) {
	await loginAsOwner(page);
	await page.route(/\/api\/models\/types/, (route) =>
		route.fulfill({ json: { success: true, data: { types: TYPES } } })
	);
	if (remote) {
		await page.route(/\/api\/backends$/, async (route) => {
			const response = await route.fetch();
			const body = await response.json();
			const data = Array.isArray(body.data) ? body.data : [];
			data.push({ id: 'rack-a', name: 'Rack A', driver: 'native.remote', configured: true });
			await route.fulfill({ response, json: { ...body, success: true, data } });
		});
	}
	await page.setViewportSize({ width, height });
	await page.goto('/admin?tab=downloads');
	await page.getByRole('button', { name: /Add (Your First )?Download/i }).first().click();
	await expect(page.locator('#url')).toBeVisible();
}

for (const [name, remote] of [['single', false], ['remote', true]] as const) {
	test(`add download dialog one column: ${name}`, async ({ page }) => {
		await openDialog(page, remote, 1440, 900);
		if (remote) {
			await expect(page.getByRole('button', { name: 'Remote', exact: true })).toBeVisible();
			await page.getByRole('button', { name: 'Remote', exact: true }).click();
			await expect(page.locator('#remote-backend')).toBeVisible();
			await expect(page.getByRole('button', { name: 'Remote', exact: true })).toHaveAttribute('aria-pressed', 'true');
			await expect(page.getByRole('button', { name: 'This machine' })).toHaveAttribute('aria-pressed', 'false');
			await expect(page.locator('[role="listbox"][aria-label="Subfolder"]')).toBeVisible();
		} else {
			await expect(page.getByRole('button', { name: 'This machine' })).toHaveCount(0);
		}
		await expect(page.getByRole('button', { name: /^checkpoint/i })).toHaveAttribute('aria-pressed', 'true');
		const url = await page.locator('#url').boundingBox();
		const type = await page.locator('[role="group"][aria-label="Model type"]').boundingBox();
		expect(Math.abs(url!.x - type!.x)).toBeLessThan(2);
		expect(Math.abs(url!.width - type!.width)).toBeLessThan(2);
		if (SHOTS) {
			await page.mouse.move(0, 0);
			await page.waitForTimeout(500);
			await page.screenshot({ path: `${SHOTS}/${name}-1440.png` });
			await page.setViewportSize({ width: 390, height: 844 });
			await page.screenshot({ path: `${SHOTS}/${name}-390.png` });
		}
	});
}
