import { test, expect } from '@playwright/test';
import { loginAsOwner, screenshot, shotPath } from './helpers';

const JOURNEY = 'chat-composer';

// Deliberate pause so the recorded clip reads well for a human reviewer.
const BEAT = 450;

// The LLM chat composer keeps its @-resource attach affordance, drops the
// slash-command picker, and gathers the tools / memory / pin controls into the
// bottom action row. This journey opens the composer and exercises each control,
// capturing a labeled screenshot of every decisive state. Assertions stay
// resilient to the exact tool list (a fresh instance with no LLM has few or no
// tools) — it asserts the popovers/panels and their fixed structure render, not
// a tool count.
test('chat composer exposes attach, tools, memory and pin controls (no command picker)', async ({ page }) => {
	await loginAsOwner(page);

	await page.goto('/models');
	// Sidebar.svelte's AI Chat trigger has no `title` attribute (its tooltip
	// text comes from a wrapping <Tooltip>, not a native title) — only its
	// accessible name, "AI Chat" (via aria-label), identifies it.
	const fab = page.getByRole('button', { name: 'AI Chat' });
	await expect(fab).toBeVisible({ timeout: 15000 });
	await fab.click();

	// The composer card (chip input + action row) renders even with no LLM
	// configured — only the send action is disabled.
	const composer = page.locator('.bg-surface-1.rounded-lg:has(button[title="Send (Enter)"])');
	await expect(composer).toBeVisible({ timeout: 15000 });
	await page.waitForTimeout(BEAT);

	await composer.screenshot({ path: shotPath(JOURNEY, 'composer-bottom-bar') });
	await screenshot(page, JOURNEY, 'chat-panel-open');

	// --- Attach affordance + no command picker (core assertions) ---
	const chipInput = composer.locator('[role="textbox"][aria-placeholder]');
	await expect(chipInput).toBeVisible();
	await expect(chipInput).toHaveAttribute('aria-placeholder', /attach a resource/i);
	const commandButton = composer.locator(
		'button[title*="command" i], button[aria-label*="command" i]'
	);
	await expect(commandButton).toHaveCount(0);

	// --- 1. Tools popover (opens upward) ---
	const toolsButton = composer.locator('button[title^="Tools"]');
	await expect(toolsButton).toBeVisible();
	const toolsTitleBefore = await toolsButton.getAttribute('title');
	await toolsButton.click();

	const toolsPopover = page.locator('div.absolute.bottom-full').filter({ hasText: 'Enable tools' });
	await expect(toolsPopover).toBeVisible();
	await expect(page.getByText('Enable tools')).toBeVisible();

	// Assert it opens upward: the popover sits above the button that triggered it.
	const popBox = await toolsPopover.boundingBox();
	const btnBox = await toolsButton.boundingBox();
	expect(popBox, 'tools popover has a box').not.toBeNull();
	expect(btnBox, 'tools button has a box').not.toBeNull();
	expect(popBox!.y, 'tools popover should open upward (above its button)').toBeLessThan(btnBox!.y);

	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, 'tools-popover-open');

	// Tools arrive grouped behind a drill-down: the top level renders one row
	// per group (tri-state checkbox + name + enabled/total count + chevron),
	// never the full per-tool list. A backend that stops sending `group`
	// collapses everything into "Other" and fails the count-of-groups check.
	const groupList = toolsPopover.locator('[data-testid="tool-group-list"]');
	await expect(groupList).toBeVisible();
	const groupRows = groupList.locator('[data-testid="tool-group-row"]');
	const groupCount = await groupRows.count();
	expect(groupCount, 'tool groups should render as top-level rows').toBeGreaterThanOrEqual(5);
	const groupNames = await groupRows.evaluateAll((rows) => rows.map((r) => r.getAttribute('data-group')));
	expect(groupNames).toContain('Memory');
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, 'tools-top-level');

	function groupRow(groupName: string) {
		return groupList.locator(`[data-testid="tool-group-row"][data-group="${groupName}"]`);
	}

	// Drill into a couple of representative groups: the first group and Memory.
	async function drillInto(groupName: string) {
		const row = groupRow(groupName);
		const countBefore = await row.locator('[data-testid="tool-group-count"]').innerText();
		await row.locator('[data-testid="tool-group-drill"]').click();

		const detail = toolsPopover.locator('[data-testid="tool-group-detail"]');
		await expect(detail).toBeVisible();
		await expect(detail.locator('[data-testid="tool-group-back"]')).toContainText(groupName);
		const toolRows = detail.locator('[data-testid="tool-row"]');
		await expect(toolRows.first()).toBeVisible();
		// Every tool row still carries its user-facing description below the name.
		expect(await toolRows.count()).toBeGreaterThan(0);
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, `tools-group-${groupName}`.toLowerCase().replace(/[^a-z0-9-]+/g, '-'));

		return { detail, toolRows, countBefore };
	}

	const firstGroupName = groupNames[0]!;
	await drillInto(firstGroupName);
	// Back out returns to the group list.
	await toolsPopover.locator('[data-testid="tool-group-back"]').click();
	await expect(groupList).toBeVisible();
	await page.waitForTimeout(BEAT);

	const memory = await drillInto('Memory');

	// --- Tri-state: toggle one tool off, back out, check indeterminate + count ---
	const [enabledBefore, totalBefore] = memory.countBefore.split('/').map(Number);
	const firstToolCheckbox = memory.toolRows.first().locator('input[type="checkbox"]');
	await firstToolCheckbox.click();
	await toolsPopover.locator('[data-testid="tool-group-back"]').click();
	await expect(groupList).toBeVisible();

	const memoryRow = groupRow('Memory');
	if (totalBefore > 1) {
		await expect(memoryRow.locator('[data-testid="tool-group-count"]')).toHaveText(
			`${enabledBefore - 1}/${totalBefore}`
		);
		const memoryCheckbox = memoryRow.locator('input[type="checkbox"]');
		expect(await memoryCheckbox.evaluate((el: HTMLInputElement) => el.indeterminate)).toBe(true);
	}
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, 'tools-tristate-indeterminate');

	// Restore: drill back in and re-toggle the same tool.
	await memoryRow.locator('[data-testid="tool-group-drill"]').click();
	const detail = toolsPopover.locator('[data-testid="tool-group-detail"]');
	await detail.locator('[data-testid="tool-row"]').first().locator('input[type="checkbox"]').click();
	await toolsPopover.locator('[data-testid="tool-group-back"]').click();
	await expect(groupList).toBeVisible();
	await page.waitForTimeout(BEAT);

	// Toggle the master "Enable tools" checkbox and show the state change (the
	// button title flips ON <-> OFF). Per-tool rows may be absent on a fresh
	// instance, so drive the always-present master toggle.
	await toolsPopover.locator('input[type="checkbox"]').first().click();
	const flipped = toolsTitleBefore?.includes('ON') ? /Tools OFF/ : /Tools ON/;
	await expect(toolsButton).toHaveAttribute('title', flipped);
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, 'tools-toggled');

	// Restore + close (click the popover's full-screen backdrop, away from it).
	await toolsPopover.locator('input[type="checkbox"]').first().click();
	await page.locator('[aria-label="Close tools selector"]').click({ position: { x: 6, y: 6 } });
	await expect(toolsPopover).toBeHidden();
	await page.waitForTimeout(BEAT);

	// --- 2. Memory panel (fixed right-side overlay) ---
	await composer.locator('button[title="Memory"]').click();
	const memoryPanel = page.locator('[aria-label="Memory"]');
	await expect(memoryPanel).toBeVisible();
	await expect(memoryPanel.getByRole('heading', { name: 'Memory' })).toBeVisible();
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, 'memory-panel-open');
	await memoryPanel.locator('button[title="Close"]').click();
	await expect(memoryPanel).toBeHidden();
	await page.waitForTimeout(BEAT);

	// --- 3. Pin-to-tab popover ---
	await composer.locator('button[title="Pin to tab"]').click();
	const pinPopover = page.locator('div.absolute.bottom-full').filter({ hasText: 'Follow active tab' });
	await expect(pinPopover).toBeVisible();
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, 'pin-popover-open');
	await page.locator('[aria-label="Close pin selector"]').click({ position: { x: 6, y: 6 } });
	await expect(pinPopover).toBeHidden();
	await page.waitForTimeout(BEAT);

	// --- 4. Final full-composer screenshot of the settled bottom row ---
	await composer.screenshot({ path: shotPath(JOURNEY, 'composer-full-row') });

	console.log(
		`[${JOURNEY}] composer controls exercised: @-attach present, command button absent, ` +
			`tools popover opens upward + master toggle flips, memory panel opens, pin popover opens`
	);
});
