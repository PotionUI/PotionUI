import { test, expect } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'workspaces-save-and-load';

// Drives the real TabBar workspace menu in a browser: save the current
// 2-tab layout as a named workspace, close the second tab, then pick the
// saved workspace back and assert both tabs are restored. HTTP-only checks
// can't see this class of bug (frontend/backend field-shape drift, a
// silently-swallowed fetch).
test('workspace save + pick round-trip restores tabs', async ({ page }) => {
	const apiErrors: string[] = [];
	page.on('response', (res) => {
		if (res.url().includes('/api/workspaces') && res.status() >= 400) {
			apiErrors.push(`${res.request().method()} ${res.url()} -> ${res.status()}`);
		}
	});

	await loginAsOwner(page);

	// Fresh instance starts with exactly one tab, "Generation 1".
	await expect(page.getByText('Generation 1', { exact: true })).toBeVisible();

	// Add a second tab so the saved workspace has more than one to restore.
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await expect(page.getByText('Generation 2', { exact: true })).toBeVisible();

	await screenshot(page, JOURNEY, 'two-tabs-before-save');

	// Open the workspace menu and save the current layout.
	await page.getByTitle('Workspaces').click();
	await page.getByRole('button', { name: 'Save current layout as new' }).click();

	const wsName = `E2E WS ${Date.now()}`;
	await page.getByPlaceholder('Workspace name...').fill(wsName);
	await page.getByRole('button', { name: 'Save as new' }).click();

	// The save modal closes and a success toast confirms the round trip
	// actually persisted (not just that the click handler ran).
	await expect(page.getByText(`Saved`, { exact: false })).toBeVisible({ timeout: 5000 });
	await expect(page.getByText(wsName, { exact: false })).toBeVisible({ timeout: 5000 });

	await screenshot(page, JOURNEY, 'after-save-toast');

	// Close the second tab so restoring the workspace is the only way to get
	// back to two tabs.
	await page
		.locator('button.book-tab', { hasText: 'Generation 2' })
		.locator('.close-button')
		.click({ force: true });
	await expect(page.getByText('Generation 2', { exact: true })).toHaveCount(0);

	// Reopen the workspace menu - this refetches the list, exercising
	// getWorkspaces() a second time.
	await page.getByTitle('Workspaces').click();
	const wsEntry = page.getByRole('button', { name: wsName, exact: true });
	await expect(wsEntry).toBeVisible({ timeout: 5000 });

	await screenshot(page, JOURNEY, 'menu-shows-saved-workspace');

	// Pick it back.
	await wsEntry.click();

	await expect(page.getByText('Generation 1', { exact: true })).toBeVisible();
	await expect(page.getByText('Generation 2', { exact: true })).toBeVisible();

	await screenshot(page, JOURNEY, 'after-load-tabs-restored');

	expect(apiErrors, `workspace API returned error responses: ${apiErrors.join(', ')}`).toEqual([]);

	console.log(`[${JOURNEY}] saved+restored workspace "${wsName}", no workspace API errors`);
});

// A legacy workspace row can have several tabs with preset_ids and predate
// the autoCollectionIds field, which the current getCurrentWorkspaceData()/
// loadWorkspace() shape now expects. Simulate that exact shape - foreign
// preset_ids the throwaway instance doesn't have installed, a null
// mode/preset_id tab, and no autoCollectionIds key at all - via the API
// (bypassing the UI's own save, so this isolates the LOAD path) and confirm
// picking it in the UI doesn't crash the page or silently no-op.
test('loading a workspace with legacy/foreign tab data does not break the page', async ({ page }) => {
	// A "preset_not_found" form-schema error is the EXPECTED, gracefully-handled
	// outcome for a tab whose preset_id no longer resolves (see the
	// "Could not load form schema" card asserted below) - only an uncaught
	// exception (pageerror) or any OTHER console error is a regression.
	const unexpectedErrors: string[] = [];
	page.on('pageerror', (err) => unexpectedErrors.push(`pageerror: ${err.message}`));
	page.on('console', (msg) => {
		if (msg.type() === 'error' && !msg.text().includes('preset_not_found')) {
			unexpectedErrors.push(`console.error: ${msg.text()}`);
		}
	});

	await loginAsOwner(page);
	const token = await ownerToken(page);

	const legacyName = `E2E Legacy ${Date.now()}`;
	const created = await page.request.post('/api/workspaces', {
		headers: { Authorization: `Bearer ${token}` },
		data: {
			name: legacyName,
			data: {
				tabs: [
					{ name: 'KREA-2', color: '#ef4444', preset_id: 'DOES-NOT-EXIST-1', mode: 'txt2img', autoTagIds: [] },
					{ name: 'Wan 2.2.', color: '#ec4899', preset_id: null, mode: null, autoTagIds: [] },
					{ name: 'COMFY', color: null, preset_id: 'DOES-NOT-EXIST-2', mode: 'txt2img', autoTagIds: [] }
				]
				// deliberately no autoCollectionIds anywhere, matching the pre-migration shape
			}
		}
	});
	expect(created.ok(), `workspace create -> ${created.status()}`).toBeTruthy();

	await page.reload();
	await expect(page.getByText('Generation 1', { exact: true })).toBeVisible();

	await page.getByTitle('Workspaces').click();
	const entry = page.getByRole('button', { name: legacyName, exact: true });
	await expect(entry).toBeVisible({ timeout: 5000 });
	await entry.click();

	// All 3 tabs must materialize even though 2 of their preset_ids don't
	// resolve to an installed preset.
	await expect(page.getByText('KREA-2', { exact: true })).toBeVisible();
	await expect(page.getByText('Wan 2.2.', { exact: true })).toBeVisible();
	await expect(page.getByText('COMFY', { exact: true })).toBeVisible();

	// The unresolvable preset_id degrades gracefully to an error card, not a
	// blank pane or a dead page.
	await expect(page.getByText('Could not load form schema')).toBeVisible();

	await screenshot(page, JOURNEY, 'legacy-workspace-loaded');

	expect(unexpectedErrors, `page threw/logged unexpected errors loading legacy workspace: ${unexpectedErrors.join('\n')}`).toEqual([]);

	console.log(`[${JOURNEY}] legacy-shaped workspace "${legacyName}" loaded 3 tabs cleanly (1 with an expected preset_not_found card), no unexpected page errors`);
});
