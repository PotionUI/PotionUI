import { test, expect, type Page, type Locator } from '@playwright/test';
import { loginAsOwner, ownerToken } from './helpers';
import { startFakeLLM, seedFakeLlmConfig, type FakeLLMServer } from './fake-llm';

// Regression coverage for the portaled-catcher-vs-non-portaled-menu teardown
// bug (see ChatContextStrip.svelte's pin picker and ChatInput.svelte's Tools
// dropdown for the fixed idiom): a `{#if open}` block whose first child was a
// portaled `use:portal` full-page click-catcher and whose second child was a
// non-portaled `absolute top-full` menu never unmounted the menu on close,
// because Svelte 5's if-block teardown walks the sibling chain from the first
// node, which the portal action had already reparented into <body>. Picking
// an item ran its handler but the dropdown stayed visible. ChatHeader's LLM
// model picker and ChatModeSelector's mode picker were the last two sites
// still on that pattern; both now portal the menu itself instead. Requires an
// enabled LLM config to exist, or UnifiedAIChat renders its "No enabled LLM
// configurations" empty state instead of the composer.

const BACKEND = process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8055';

let fake: FakeLLMServer;

test.beforeEach(async () => {
	fake = await startFakeLLM();
});

test.afterEach(async () => {
	await fake.close();
});

async function openChat(page: Page): Promise<Locator> {
	await page.goto('/models');
	const fab = page.getByRole('button', { name: 'AI Chat' });
	await expect(fab).toBeVisible({ timeout: 15000 });
	await fab.click();
	const composer = page.locator('.bg-surface-1.rounded-lg:has(button[title="Send (Enter)"])');
	await expect(composer).toBeVisible({ timeout: 15000 });
	return composer;
}

test.describe('chat header pickers', () => {
	test('LLM model picker closes and unmounts after picking a config', async ({ page }) => {
		await loginAsOwner(page);
		const token = await ownerToken(page);
		await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);
		await openChat(page);

		const trigger = page.locator('[data-testid="chat-header-model-trigger"]');
		await expect(trigger).toBeVisible({ timeout: 15000 });
		await trigger.click();

		const menu = page.locator('[data-testid="chat-header-model-menu"]');
		await expect(menu).toBeVisible({ timeout: 5000 });

		await menu.locator('button').first().click();

		// The menu itself must actually leave the DOM, not just become
		// visually hidden - this is the exact defect the portal teardown
		// trap caused.
		await expect(menu).toHaveCount(0);
	});

	test('mode picker closes and unmounts after picking a mode', async ({ page }) => {
		await loginAsOwner(page);
		const token = await ownerToken(page);
		await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);
		await openChat(page);

		const trigger = page.locator('[data-testid="chat-mode-selector-trigger"]');
		await expect(trigger).toBeVisible({ timeout: 15000 });
		await trigger.click();

		const menu = page.locator('[data-testid="chat-mode-selector-menu"]');
		await expect(menu).toBeVisible({ timeout: 5000 });

		await menu.locator('button').first().click();

		await expect(menu).toHaveCount(0);
	});
});
