import { test, expect, type Request } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { screenshot, shotPath } from './helpers';

const JOURNEY = 'fresh-install-guided-setup';
const RECIPE_NAME = process.env.E2E_FRESH_RECIPE_NAME ?? '';
const PASSWORD = 'fresh-owner-pw-7c1e44';

test.use({ viewport: { width: 1440, height: 900 } });

test('a fresh instance runs Guided setup from the real wizard', async ({ page }) => {
	test.setTimeout(240000);
	const calls: string[] = [];
	const record = (request: Request) => {
		const url = new URL(request.url());
		if (!url.pathname.startsWith('/api/')) return;
		const line = `${request.method()} ${url.pathname}`;
		if (calls[calls.length - 1] !== line) calls.push(line);
	};
	page.on('request', record);
	await page.route('**/api/setup/machine', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				gpu: { name: 'NVIDIA GeForce RTX 4090', vram_bytes: 25757220864, compute_capability: '8.9', supported: true },
				ram_total_bytes: 68719476736,
				models_disk: null
			})
		})
	);

	try {
		await page.goto('/');
		await expect(page.getByRole('heading', { name: 'A local studio for AI images and video' })).toBeVisible({
			timeout: 20000
		});
		await screenshot(page, JOURNEY, '01-welcome');
		await page.getByRole('button', { name: 'Get started' }).click();

		await expect(page.getByRole('heading', { name: 'Create your owner account' })).toBeVisible();
		await expect(page.getByText('This account lives only on this computer.')).toBeVisible();
		await screenshot(page, JOURNEY, '02-claim-owner');

		await page.locator('#username').fill('fresh-owner');
		await page.locator('#email').fill('fresh-owner@example.com');
		await page.locator('#password').fill(PASSWORD);
		await page.locator('#confirmPassword').fill(PASSWORD);
		await page.getByRole('button', { name: 'Create owner account' }).click();

		await page.waitForURL(/\/setup$/, { timeout: 20000 });
		await expect(page.getByRole('heading', { name: 'Your computer' })).toBeVisible({ timeout: 20000 });
		await expect(page.getByRole('button', { name: 'Skip the guide, go to the app' })).toBeVisible();
		await screenshot(page, JOURNEY, '03-computer');
		await page.getByRole('button', { name: 'Continue' }).click();

		await expect(page.getByRole('heading', { name: 'Where should PotionUI keep your models?' })).toBeVisible();
		await expect(page.getByRole('radio', { name: /Start fresh/ })).toHaveAttribute('aria-checked', 'true');
		await screenshot(page, JOURNEY, '04-models-location');
		await page.getByRole('button', { name: 'Continue' }).click();

		await expect(page.getByRole('heading', { name: 'Pick a starter model' })).toBeVisible({ timeout: 20000 });
		await expect(page.getByText(RECIPE_NAME)).toBeVisible({ timeout: 20000 });
		await screenshot(page, JOURNEY, '05-starter');

		await page.getByRole('radio', { name: new RegExp(RECIPE_NAME) }).click();
		const review = page.getByRole('button', { name: /^Review( .+)? download$/ });
		await expect(async () => {
			if ((await review.count()) > 0 && (await review.isEnabled())) await review.click();
			await expect(page.getByRole('heading', { name: 'Review the download' })).toBeVisible({ timeout: 8000 });
		}).toPass({ timeout: 60000, intervals: [500] });
		await expect(page.getByText("Couldn't finish")).toHaveCount(0);
		await expect(page.getByText(/needs to download the following/)).toBeVisible({ timeout: 60000 });
		await expect(page.getByText(/Downloads come from Hugging Face \(huggingface\.co\)/)).toBeVisible();
		const tokenDisclosure = page.locator('details', { hasText: /Add a token \(optional\)/ });
		await expect(tokenDisclosure).toBeVisible();
		await expect(tokenDisclosure).not.toHaveJSProperty('open', true);
		await screenshot(page, JOURNEY, '06-consent');
		await tokenDisclosure.locator('summary').click();
		await expect(tokenDisclosure.getByRole('button', { name: 'Save' })).toBeDisabled();
		await expect(tokenDisclosure.getByText('Needed for gated models.')).toBeVisible();
		await screenshot(page, JOURNEY, '07-consent-token');
		await page.getByRole('button', { name: 'Approve and download' }).click();

		await expect(page.getByRole('heading', { name: "You're all set" })).toBeVisible({ timeout: 240000 });
		await expect(page.getByText("Setup couldn't finish")).toHaveCount(0);
		await expect(page.getByRole('button', { name: 'Create your first image' })).toBeVisible();
		await screenshot(page, JOURNEY, '08-ready');

		await page.getByText('Readiness checks', { exact: true }).click();
		await page.getByRole('button', { name: 'Refresh' }).click();
		const presetsCheck = page.locator('div').filter({ has: page.getByRole('heading', { name: 'Presets & models' }) }).last();
		await expect(presetsCheck.getByText('ready', { exact: true })).toBeVisible({ timeout: 30000 });
		await screenshot(page, JOURNEY, '09-presets-ready');

		await page.goto('/generate');
		await page.getByRole('button', { name: 'Choose a preset' }).click();
		await expect(page.getByText('Flux2', { exact: false }).first()).toBeVisible({ timeout: 15000 });
		await screenshot(page, JOURNEY, '10-generate');

		await page.setViewportSize({ width: 390, height: 844 });
		await screenshot(page, JOURNEY, '11-completed-390');
	} finally {
		const path = shotPath(JOURNEY, 'api-calls').replace(/\.png$/, '.txt');
		mkdirSync(dirname(path), { recursive: true });
		writeFileSync(path, calls.join('\n') + '\n');
		console.log(`API calls:\n${calls.join('\n')}`);
	}
});
