import { test, expect } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { startFakeLLM, seedFakeLlmConfig, type FakeLLMServer } from './fake-llm';

const JOURNEY = 'chat-composer-draft';
const BEAT = 350;
const BACKEND = process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8055';

// GlobalChatPanel.svelte unmounts UnifiedAIChat on close (only the sliding
// shell stays), which used to destroy the composer's local state — typing a
// draft, then closing the drawer before sending, lost the text. Covers the
// fix: chatComposerDrafts (a module-scope store, same trick as chatSession)
// survives the unmount and UnifiedAIChat restores from it on mount.

let fake: FakeLLMServer;

test.beforeEach(async () => {
	fake = await startFakeLLM();
});

test.afterEach(async () => {
	await fake.close();
});

test('a composer draft survives closing the chat drawer and is restored on reopen', async ({ page }) => {
	test.setTimeout(120000);
	await loginAsOwner(page);
	const token = await ownerToken(page);
	await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);

	await page.goto('/generate');
	// Sidebar.svelte's AI Chat trigger has no `title` attribute (its tooltip
	// text comes from a wrapping <Tooltip>, not a native title) — only its
	// accessible name, "AI Chat" (via aria-label), identifies it.
	const fab = page.getByRole('button', { name: 'AI Chat' });
	await expect(fab).toBeVisible({ timeout: 15000 });
	await fab.click();

	const composer = page.locator('.bg-surface-1.rounded-lg:has(button[title="Send (Enter)"])');
	await expect(composer).toBeVisible({ timeout: 15000 });
	const chipInput = composer.locator('[role="textbox"][aria-placeholder]');
	await chipInput.click();
	await page.keyboard.type('Draft I do not want to lose');
	await expect(chipInput).toContainText('Draft I do not want to lose');
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, '00-drafted-before-close');

	// Close via the header's X button (title="Close (Esc)") — the path that
	// used to reset userInput itself, on top of the unmount.
	await page.locator('button[title="Close (Esc)"]').click();
	await expect(composer).toBeHidden();
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, '01-drawer-closed');

	// Reopen via the sidebar AI Chat button — a fresh UnifiedAIChat instance.
	await fab.click();
	const reopenedComposer = page.locator('.bg-surface-1.rounded-lg:has(button[title="Send (Enter)"])');
	await expect(reopenedComposer).toBeVisible({ timeout: 15000 });
	const reopenedChipInput = reopenedComposer.locator('[role="textbox"][aria-placeholder]');
	await expect(reopenedChipInput).toContainText('Draft I do not want to lose');
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, '02-draft-restored');

	// Sending clears the draft — closing and reopening again must not bring
	// back the message just sent.
	fake.enqueue({ kind: 'text', text: 'Acknowledged.' });
	await reopenedComposer.locator('button[title="Send (Enter)"]').click();
	await expect(page.getByText('Acknowledged.')).toBeVisible({ timeout: 30000 });
	await expect(reopenedChipInput).toHaveText('');
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, '03-sent-composer-empty');

	await page.locator('button[title="Close (Esc)"]').click();
	await expect(reopenedComposer).toBeHidden();
	await fab.click();
	const finalComposer = page.locator('.bg-surface-1.rounded-lg:has(button[title="Send (Enter)"])');
	await expect(finalComposer).toBeVisible({ timeout: 15000 });
	const finalChipInput = finalComposer.locator('[role="textbox"][aria-placeholder]');
	await expect(finalChipInput).toHaveText('');
	await screenshot(page, JOURNEY, '04-no-stale-draft-after-send');
});
