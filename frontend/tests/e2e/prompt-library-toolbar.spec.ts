import { test, expect } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'prompt-library-toolbar';

// Prompt Library v3: the page-level toolbar replaced the old pane "New
// prompt" button; the four section tabs (Prompts / Segments / Segment
// Templates / Segment Categories) stay permanently visible in the header.
// Two things must keep working: (1) the toolbar's composer selects the
// prompt it just created, and (2) the Segments tab swaps in its workspace
// and the Prompts tab comes back to the same list.

test('toolbar New prompt selects the created prompt, and the section tabs round-trip', async ({ page }) => {
	test.setTimeout(60000);
	await loginAsOwner(page);

	await page.goto('/prompts');
	await expect(page.getByRole('button', { name: 'New prompt' })).toBeVisible({ timeout: 15000 });

	await page.getByRole('button', { name: 'New prompt' }).click();
	await expect(page.getByRole('heading', { name: 'New prompt' })).toBeVisible({ timeout: 10000 });

	const uniqueName = `E2E toolbar prompt ${Date.now()}`;
	await page.getByPlaceholder('Content preview is used when unnamed').fill(uniqueName);
	await page
		.getByPlaceholder('Paste anything — a whole prompt, notes, a description...')
		.fill('toolbar-composed content for the selection check');

	const createResponse = page.waitForResponse(
		(r) => r.url().includes('/api/prompts') && r.request().method() === 'POST',
		{ timeout: 15000 }
	);
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await createResponse;

	await expect(page.getByRole('heading', { name: 'Edit Prompt' })).toBeVisible({ timeout: 10000 });
	// Selected: the detail pane's own Name field carries the same value, not
	// just the list row - proves handlePromptCreated actually selected it
	// rather than merely refreshing the list.
	await expect(page.getByPlaceholder('Content preview is used when unnamed')).toHaveValue(uniqueName, {
		timeout: 10000
	});

	await screenshot(page, JOURNEY, 'created-and-selected');

	// Section tabs: always visible in the header - Segments swaps in its
	// workspace, Prompts comes back to the same list (still showing the
	// prompt just created).
	await page.getByRole('button', { name: 'Segments', exact: true }).click();
	await expect(page.getByText('Saved Segments', { exact: true })).toBeVisible({ timeout: 10000 });

	await screenshot(page, JOURNEY, 'segments-tab-open');

	await page.getByRole('button', { name: 'Prompts', exact: true }).click();
	await expect(page.getByRole('button', { name: 'New prompt' })).toBeVisible({ timeout: 10000 });
	await expect(page.getByText(uniqueName)).toBeVisible({ timeout: 10000 });

	console.log(`[${JOURNEY}] toolbar composer selected the new prompt, section tabs round-tripped cleanly`);
});

// Core ships its own file/text import source, so the Import button is always
// visible. With no plugin importer registered, clicking it skips the dropdown
// (nothing else to list) and opens the core modal directly.
test('toolbar Import button opens the core file/text importer when no plugin importer is enabled', async ({
	page
}) => {
	test.setTimeout(30000);
	await loginAsOwner(page);

	await page.goto('/prompts');
	await expect(page.getByRole('button', { name: 'New prompt' })).toBeVisible({ timeout: 15000 });

	const importButton = page.getByRole('button', { name: 'Import', exact: true });
	await expect(importButton).toBeVisible();
	await importButton.click();

	await expect(page.getByRole('heading', { name: 'Import prompts' })).toBeVisible({ timeout: 10000 });

	console.log(`[${JOURNEY}] Import button opens the core importer with no registered plugin importers`);
});
