import { test, expect } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'empty-group-tabs';

// In browser form: a user group with zero assignments, opened on its
// Presets tab, must settle — no stuck spinner, an empty state, and the
// /api/user-groups/{id}/presets fetch must plateau (the reactivity loop refired
// it forever). HTTP can't see the loop; a real browser can.
test('empty group Presets tab settles without a request loop', async ({ page }) => {
	const groupRequests: { url: string; at: number }[] = [];
	page.on('request', (req) => {
		if (req.url().includes('/api/user-groups/')) {
			groupRequests.push({ url: req.url(), at: Date.now() });
		}
	});

	await loginAsOwner(page);
	const token = await ownerToken(page);

	// Create the empty group via the API (proxied through the preview origin).
	const groupName = `e2e-empty-${Date.now()}`;
	const created = await page.request.post('/api/user-groups/', {
		headers: { Authorization: `Bearer ${token}` },
		data: { name: groupName, description: null }
	});
	expect(created.ok(), `group create -> ${created.status()}`).toBeTruthy();
	const groupId = (await created.json())?.data?.id as string;
	expect(groupId, 'group create returned an id').toBeTruthy();

	// Admin → Users tab → Groups sub-view.
	await page.goto('/admin?tab=users');
	await page.locator('nav[aria-label="Users / Groups views"]').getByRole('button', { name: /Groups/ }).click();

	const groupList = page.locator('[role="listbox"][aria-label="Groups"]');
	await expect(groupList).toBeVisible({ timeout: 15000 });
	await groupList.getByText(groupName, { exact: true }).click();

	// Group detail → Presets tab.
	const detailTabs = page.locator('nav[aria-label="Group details"]');
	await expect(detailTabs).toBeVisible();

	const presetsUrl = `/api/user-groups/${groupId}/presets`;
	const countPresetFetches = () => groupRequests.filter((r) => r.url.includes(presetsUrl)).length;

	await detailTabs.getByRole('button', { name: /Presets/ }).click();

	// Let any loop have time to run away.
	await page.waitForTimeout(3000);
	const afterSettle = countPresetFetches();

	// Trailing quiet window: no NEW presets fetch may fire once settled.
	await page.waitForTimeout(2000);
	const afterQuiet = countPresetFetches();

	const spinner = page.locator('[role="status"][aria-label="Loading"]');
	const emptyState = page.getByText('No presets installed', { exact: true });

	await screenshot(page, JOURNEY, 'group-presets-settled');

	// 1. No stuck spinner.
	await expect(spinner).toHaveCount(0);
	// 2. Empty state rendered (a fresh instance has no installed presets).
	await expect(emptyState).toBeVisible();
	// 3. Network went quiet — the presets fetch plateaued.
	expect(afterQuiet, `presets fetch kept firing after settle (${afterSettle} -> ${afterQuiet})`).toBe(afterSettle);
	expect(afterQuiet, `presets endpoint hit too many times (${afterQuiet}) — looks like a loop`).toBeLessThanOrEqual(3);

	console.log(
		`[${JOURNEY}] presets fetches=${afterQuiet} (plateaued at ${afterSettle}), ` +
			`total /api/user-groups/ requests=${groupRequests.length}, spinner gone, empty state shown`
	);
});
