import { test, expect, type Page, type Locator } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { startFakeLLM, seedFakeLlmConfig, type FakeLLMServer } from './fake-llm';

// Coverage for the chat tab-context strip (ChatContextStrip.svelte, driven by
// $lib/chat/contextStrip.ts): the always-visible "Reading <tab>" strip, its
// own tab picker (portaled dropdown anchored to the tab name), and the
// composer's plain pin/unpin toggle. Requires an enabled LLM config, same
// gate as pin-dropdown-repro.spec.ts.
//
// NOTE ON COVERAGE: GlobalChatPanel's backdrop (`.fixed.inset-0`, z-40) sits
// above the whole /generate page, including TabBar, while the chat drawer is
// open -- so a live "switch the active Generate tab while the panel stays
// open" (the transcript-divider + strip-flash moment) isn't reachable through
// real clicks today; that path is unit-tested instead (see
// contextStrip.test.ts's deriveTabSwitchDivider cases). Every state reachable
// without clicking through that backdrop is covered here: pinning to the
// active tab (pinned-active) and pinning to a different one (pinned-mismatch)
// both happen via the strip's own picker, entirely inside the open panel, so
// neither needs the tab bar to be clickable.

const BACKEND = process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8055';
const JOURNEY = 'chat-tab-context';

let fake: FakeLLMServer;

test.beforeEach(async () => {
	fake = await startFakeLLM();
});

test.afterEach(async () => {
	await fake.close();
});

function strip(page: Page): Locator {
	return page.locator('[data-testid="chat-context-strip"]');
}

function stripTrigger(page: Page): Locator {
	return strip(page).locator('button').first();
}

function picker(page: Page): Locator {
	return page.locator('[data-testid="chat-context-strip-picker"]');
}

async function pinToTab(page: Page, tabName: string): Promise<void> {
	await stripTrigger(page).click();
	const dropdown = picker(page);
	await expect(dropdown).toBeVisible({ timeout: 5000 });
	await dropdown.getByText(tabName, { exact: true }).click();
	await expect(dropdown).toHaveCount(0);
}

test('strip follows the active tab, and its picker resolves pinned-active and pinned-mismatch', async ({
	page
}) => {
	test.setTimeout(120000);
	await loginAsOwner(page);
	const token = await ownerToken(page);
	await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);

	// --- Add a second tab first (tab bar is only reachable while chat is
	// closed -- see the file header note). Adding makes it the active tab.
	await page.goto('/generate');
	await expect(page.locator('.book-tab').filter({ hasText: 'Generation 1' })).toBeVisible({
		timeout: 15000
	});
	await page.locator('button[aria-label="Add new tab"]').click();
	await expect(page.locator('.book-tab').filter({ hasText: 'Generation 2' })).toBeVisible({
		timeout: 10000
	});

	// --- Open chat: strip follows "Generation 2" (the tab just made active).
	const fab = page.getByRole('button', { name: 'AI Chat' });
	await expect(fab).toBeVisible({ timeout: 15000 });
	await fab.click();
	const composer = page.locator('.bg-surface-1.rounded-lg:has(button[title="Send (Enter)"])');
	await expect(composer).toBeVisible({ timeout: 15000 });

	const contextStrip = strip(page);
	await expect(contextStrip).toBeVisible({ timeout: 15000 });
	await expect(contextStrip).toHaveAttribute('data-strip-state', 'following');
	await expect(contextStrip).toContainText('Reading tab:');
	await expect(contextStrip).toContainText('Generation 2');
	await screenshot(page, JOURNEY, '00-following-at-rest');

	// --- Pick from the strip's own picker: explainer + preset-less subtitle-free rows are present.
	await stripTrigger(page).click();
	const dropdown = picker(page);
	await expect(dropdown).toBeVisible({ timeout: 5000 });
	await expect(dropdown).toContainText('Chat always reads whichever tab is open');
	await expect(dropdown.getByText('Generation 1', { exact: true })).toBeVisible();
	await expect(dropdown.getByText('Generation 2', { exact: true })).toBeVisible();
	await screenshot(page, JOURNEY, '01-picker-open');
	await dropdown.getByText('Generation 2', { exact: true }).click();
	await expect(dropdown).toHaveCount(0);

	// --- Pinning to the currently-active tab (Generation 2) -> pinned-active.
	await expect(contextStrip).toHaveAttribute('data-strip-state', 'pinned-active');
	await expect(contextStrip).toContainText('Pinned to');
	await expect(contextStrip).toContainText('Generation 2');
	await expect(contextStrip).toContainText('active');
	await screenshot(page, JOURNEY, '02-pinned-active');

	// --- Pinning to a DIFFERENT tab than the active one -> pinned-mismatch,
	// entirely inside the open panel (no tab-bar click needed).
	await pinToTab(page, 'Generation 1');
	await expect(contextStrip).toHaveAttribute('data-strip-state', 'pinned-mismatch', { timeout: 10000 });
	await expect(contextStrip).toContainText('Pinned to');
	await expect(contextStrip).toContainText('Generation 1');
	await expect(contextStrip).toContainText('Generate shows');
	await expect(contextStrip).toContainText('Generation 2');
	const switchButton = contextStrip.getByRole('button', { name: /Switch Generate to Generation 1/ });
	const unpinFollowButton = contextStrip.getByRole('button', { name: /Unpin.*follow Generation 2/ });
	await expect(switchButton).toBeVisible();
	await expect(unpinFollowButton).toBeVisible();
	await screenshot(page, JOURNEY, '03-pinned-mismatch');

	// --- "Switch Generate to X" re-activates the pinned tab, resolving the mismatch.
	await switchButton.click();
	await expect(contextStrip).toHaveAttribute('data-strip-state', 'pinned-active', { timeout: 10000 });
	await expect(contextStrip).toContainText('Generation 1');
	await screenshot(page, JOURNEY, '04-resolved-via-switch');

	// --- Re-create the mismatch, then resolve it the other way: unpin & follow.
	await pinToTab(page, 'Generation 2');
	await expect(contextStrip).toHaveAttribute('data-strip-state', 'pinned-mismatch', { timeout: 10000 });
	await contextStrip.getByRole('button', { name: /Unpin.*follow Generation 1/ }).click();
	await expect(contextStrip).toHaveAttribute('data-strip-state', 'following', { timeout: 10000 });
	await expect(contextStrip).toContainText('Generation 1');
	await screenshot(page, JOURNEY, '05-resolved-via-unpin');

	// --- Composer's own pin toggle pins/unpins the tab the strip is reading
	// (Generation 1, since we're following again), with no dropdown at all.
	const pinToggle = page.locator('button[title^="Pin to "], button[title^="Unpin from "]');
	await expect(pinToggle).toHaveAttribute('title', 'Pin to Generation 1');
	await pinToggle.click();
	await expect(pinToggle).toHaveAttribute('title', 'Unpin from Generation 1');
	await expect(contextStrip).toHaveAttribute('data-strip-state', 'pinned-active');
	await screenshot(page, JOURNEY, '06-composer-toggle-pinned');
	await pinToggle.click();
	await expect(pinToggle).toHaveAttribute('title', 'Pin to Generation 1');
	await expect(contextStrip).toHaveAttribute('data-strip-state', 'following');
});
