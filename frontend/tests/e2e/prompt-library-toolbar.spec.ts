import { test, expect } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'prompt-library-toolbar';

test('toolbar New prompt selects the created prompt, and the section tabs round-trip', async ({ page }) => {
	test.setTimeout(60000);
	await loginAsOwner(page);

	await page.goto('/prompts');
	await expect(page.getByRole('button', { name: 'New prompt' })).toBeVisible({ timeout: 15000 });

	await page.getByRole('button', { name: 'New prompt' }).click();
	await expect(page.getByRole('heading', { name: 'New prompt' })).toBeVisible({ timeout: 10000 });

	const uniqueName = `E2E toolbar prompt ${Date.now()}`;
	await page.getByPlaceholder('Content preview is used when unnamed').fill(uniqueName);
	const segmentEditor = page.locator('.inline-chip-editor[role="textbox"]').first();
	await expect(segmentEditor).toBeVisible({ timeout: 10000 });
	await segmentEditor.click();
	await page.keyboard.type('toolbar-composed content for the selection check');

	const createButton = page.getByRole('button', { name: 'Create', exact: true });
	await expect(createButton).toBeEnabled({ timeout: 10000 });

	const createResponse = page.waitForResponse(
		(r) => r.url().includes('/api/prompts') && r.request().method() === 'POST',
		{ timeout: 15000 }
	);
	await createButton.click();
	await createResponse;

	await expect(page.getByRole('heading', { name: uniqueName })).toBeVisible({ timeout: 10000 });
	await expect(page.getByPlaceholder('Content preview is used when unnamed')).toHaveValue(uniqueName, {
		timeout: 10000
	});
	await expect(page.getByText('No model', { exact: true })).toBeVisible({ timeout: 10000 });

	await screenshot(page, JOURNEY, 'created-and-selected');

	const sections = page.getByRole('listbox', { name: 'Library sections' });
	await sections.getByRole('option', { name: 'Segments' }).click();
	await expect(page.getByRole('button', { name: 'New segment' })).toBeVisible({ timeout: 10000 });
	await expect(page).toHaveURL(/section=segments/);

	await screenshot(page, JOURNEY, 'segments-tab-open');

	await sections.getByRole('option', { name: 'Prompts' }).click();
	await expect(page.getByRole('button', { name: 'New prompt' })).toBeVisible({ timeout: 10000 });
	await expect(page.getByText(uniqueName)).toBeVisible({ timeout: 10000 });

	console.log(`[${JOURNEY}] toolbar composer selected the new prompt, section tabs round-tripped cleanly`);
});

test('toolbar More actions menu opens the core file/text importer', async ({
	page
}) => {
	test.setTimeout(30000);
	await loginAsOwner(page);

	await page.goto('/prompts');
	await expect(page.getByRole('button', { name: 'New prompt' })).toBeVisible({ timeout: 15000 });

	await page.locator('header').getByRole('button', { name: 'More actions' }).click();
	const importItem = page.getByRole('menuitem', { name: 'From file or text' });
	await expect(importItem).toBeVisible();
	await importItem.click();

	await expect(page.getByRole('heading', { name: 'Import prompts' })).toBeVisible({ timeout: 10000 });

	console.log(`[${JOURNEY}] More actions menu opens the core importer`);
});
