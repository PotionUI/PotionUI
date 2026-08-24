import { test, expect } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'prompt-manual-create';

// Prompt Library toolbar -> New prompt composer -> paste content, name it,
// Save: a prompt typed by hand (never imported) must persist and show up in
// the list, exactly like an imported one does.

test('a manually authored prompt saves and appears in the list', async ({ page }) => {
	test.setTimeout(60000);
	await loginAsOwner(page);

	await page.goto('/prompts');
	await expect(page.getByRole('button', { name: 'New prompt' }).first()).toBeVisible({
		timeout: 15000
	});

	await page.getByRole('button', { name: 'New prompt' }).first().click();
	await expect(page.getByRole('heading', { name: 'New prompt' })).toBeVisible({ timeout: 10000 });

	const uniqueName = `E2E manual prompt ${Date.now()}`;
	await page.getByPlaceholder('Content preview is used when unnamed').fill(uniqueName);
	await page
		.getByPlaceholder('Paste anything — a whole prompt, notes, a description...')
		.fill('a hand-typed manual prompt, not imported');

	await page.waitForTimeout(300);
	await screenshot(page, JOURNEY, 'filled-before-save');

	const createResponse = page.waitForResponse(
		(r) => r.url().includes('/api/prompts') && r.request().method() === 'POST',
		{ timeout: 15000 }
	);

	const saveButton = page.getByRole('button', { name: 'Save', exact: true });
	await expect(saveButton).toBeEnabled();
	await saveButton.click();

	const response = await createResponse;
	expect(response.ok(), `POST /api/prompts -> ${response.status()}`).toBeTruthy();
	const body = await response.json();
	expect(body.success, `create must succeed: ${JSON.stringify(body)}`).toBeTruthy();
	expect(
		body.data?.segments?.[0]?.content,
		`saved segment content must carry the typed text: ${JSON.stringify(body.data)}`
	).toContain('a hand-typed manual prompt, not imported');

	await expect(page.getByText('Prompt saved')).toBeVisible({ timeout: 5000 });
	await expect(page.getByText(uniqueName)).toBeVisible({ timeout: 10000 });

	await page.waitForTimeout(300);
	await screenshot(page, JOURNEY, 'saved-in-list');

	// The list shows this prompt tagged "manual" (sourceLabel()'s fallback for
	// a falsy source_provider) - the Source filter's own "Manual" option must
	// actually find it, not just display the label.
	const listResponse = page.waitForResponse(
		(r) => r.url().includes('/api/prompts') && r.request().method() === 'GET',
		{ timeout: 15000 }
	);
	await page.getByLabel('Source').selectOption('manual');
	await listResponse;
	await expect(
		page.getByText(uniqueName),
		'a hand-typed prompt must still be found once the Source filter is set to "Manual"'
	).toBeVisible({ timeout: 10000 });

	console.log(`[${JOURNEY}] manual prompt created via POST ${response.status()}, visible in list and under the Manual source filter`);
});
