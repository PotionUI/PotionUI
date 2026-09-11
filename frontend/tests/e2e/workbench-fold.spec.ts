import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken } from './helpers';

// Folding the docked workbench pane (`toggle_workbench_panel` keybinding,
// default `e`) mirrors the existing left generation-form fold
// (`toggle_left_panel`): the pane becomes a compact rail with an expand
// control, the fold state persists per tab, and it is independent of the
// floating-workbench overlay (`toggle_floating_workbench`, default `w`) —
// closing that overlay never un-collapses the docked pane, and the rail
// stays visible under the overlay while it is open.
//
// This spec exercises the three-pane layout, where the collapsed rail is a
// vertical strip (width-based) — switched to via the view-layout picker in
// the tabs overflow menu.
//
// GenerationPanels (and the workbench inside it) only renders once a preset
// AND mode are selected, so this installs and selects a real preset first,
// the same way floating-workbench.spec.ts does.

const JOURNEY = 'workbench-fold';
// The collapsed rail is a header-style strip (w-8, 32px) with a label + chevron,
// wide enough to click but still unambiguously a rail rather than an expanded pane.
const RAIL_MAX_WIDTH_PX = 40;

async function apiGet(page: Page, url: string, token: string) {
	const res = await page.request.get(url, { headers: { Authorization: `Bearer ${token}` } });
	expect(res.ok(), `GET ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

async function apiPost(page: Page, url: string, token: string, data?: unknown) {
	const res = await page.request.post(url, {
		headers: { Authorization: `Bearer ${token}` },
		data: data ?? {}
	});
	expect(res.ok(), `POST ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

test('folding the docked workbench pane persists, frees space for prompts, and coexists with the floating overlay', async ({ page }) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);

	const me = await apiGet(page, '/api/auth/me', token);
	const userId = me.data.id as string;

	const list = await apiGet(page, '/api/presets?include_uninstalled=true', token);
	const presets = (list.data || []) as Array<{
		id: string;
		name: string;
		engine?: string;
		category?: string;
		installed?: boolean;
	}>;
	const preset =
		presets.find((p) => /sdxl/i.test(p.name)) ||
		presets.find((p) => p.engine === 'native' && p.category === 'image');

	if (!preset) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return;
	}

	if (!preset.installed) {
		await apiPost(page, `/api/presets/${preset.id}/install`, token);
	}
	await apiPost(page, `/api/presets/${preset.id}/assign`, token, { user_ids: [userId] });

	await page.goto('/generate');
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByText(preset.name, { exact: true }).first().click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();

	const tablist = page.locator('[role="tablist"]').first();
	await expect(tablist).toBeVisible({ timeout: 20000 });

	// Switch to the three-pane layout so the fold is the vertical rail.
	await page.getByRole('button', { name: 'More view options' }).click();
	await page.getByRole('option', { name: /Three panes/ }).click();

	const workbenchPane = page.locator('[data-testid="workbench-pane"]');
	const promptsPane = page.locator('[data-testid="prompts-pane"]');
	await expect(workbenchPane).toBeVisible({ timeout: 10000 });
	const dockedBox = await workbenchPane.boundingBox();
	expect(dockedBox).not.toBeNull();
	expect(dockedBox!.width).toBeGreaterThan(RAIL_MAX_WIDTH_PX);
	const promptsBoxBefore = await promptsPane.boundingBox();
	expect(promptsBoxBefore).not.toBeNull();

	// Collapse by clicking the prompts/workbench separator — the three-pane
	// layout has no other collapse control.
	await page.locator('[data-testid="prompts-workbench-handle"]').click();
	await expect(page.getByRole('button', { name: 'Collapse workbench' })).toHaveCount(0);

	const collapsedBox = await workbenchPane.boundingBox();
	expect(collapsedBox).not.toBeNull();
	expect(collapsedBox!.width).toBeLessThanOrEqual(RAIL_MAX_WIDTH_PX);

	const promptsBoxAfter = await promptsPane.boundingBox();
	expect(promptsBoxAfter).not.toBeNull();
	expect(promptsBoxAfter!.width).toBeGreaterThan(promptsBoxBefore!.width);

	// Reload: the fold is persisted per tab, so it survives.
	await page.waitForTimeout(600); // let the debounced localStorage save flush
	await page.reload();
	await expect(tablist).toBeVisible({ timeout: 20000 });
	await expect(workbenchPane).toBeVisible({ timeout: 10000 });
	const reloadedBox = await workbenchPane.boundingBox();
	expect(reloadedBox).not.toBeNull();
	expect(reloadedBox!.width).toBeLessThanOrEqual(RAIL_MAX_WIDTH_PX);

	// Opening the floating workbench keeps the docked rail exactly as-is —
	// no "Workbench is floating" placeholder to fold instead.
	await page.keyboard.press('w');
	const overlay = page.getByRole('dialog', { name: 'Workbench' });
	await expect(overlay).toBeVisible({ timeout: 10000 });
	await expect(workbenchPane).toBeVisible();
	expect((await workbenchPane.boundingBox())!.width).toBeLessThanOrEqual(RAIL_MAX_WIDTH_PX);

	// Closing the floating overlay never un-collapses the docked pane.
	await page.keyboard.press('Escape');
	await expect(overlay).not.toBeVisible({ timeout: 10000 });
	expect((await workbenchPane.boundingBox())!.width).toBeLessThanOrEqual(RAIL_MAX_WIDTH_PX);

	// Expand via the rail's own button.
	await page.getByRole('button', { name: 'Expand workbench' }).click();

	// A plain click on the prompts/workbench resize handle folds the pane too
	// (a drag beyond a few pixels still resizes); the rail expands it back.
	const handle = page.locator('[data-testid="prompts-workbench-handle"]');
	await expect(handle).toBeVisible();
	await handle.click();
	await expect(page.getByRole('button', { name: 'Expand workbench' })).toBeVisible();
	await expect(handle).toHaveCount(0);
	await page.getByRole('button', { name: 'Expand workbench' }).click();
	await expect(handle).toBeVisible();
	const expandedBox = await workbenchPane.boundingBox();
	expect(expandedBox).not.toBeNull();
	expect(expandedBox!.width).toBeGreaterThan(RAIL_MAX_WIDTH_PX);
});
