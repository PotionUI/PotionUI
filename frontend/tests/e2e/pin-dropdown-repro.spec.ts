import { test, expect, type Page, type Locator } from '@playwright/test';
import { loginAsOwner, ownerToken } from './helpers';
import { startFakeLLM, seedFakeLlmConfig, type FakeLLMServer } from './fake-llm';

const BACKEND = process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8055';

// Regression coverage for the pin/tools dropdowns in ChatInput.svelte
// (frontend/src/lib/components/chat/ChatInput.svelte). Both used to build a
// portaled full-page click-catcher next to a non-portaled menu: on desktop the
// catcher unmounted on close but the menu stayed in the DOM forever, and on
// mobile (chat embedded in the generate page's transformed swipe carousel)
// the catcher painted above the menu and absorbed every tap. Requires an
// enabled LLM config to exist, or UnifiedAIChat renders its "No enabled LLM
// configurations" empty state instead of the composer.

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

function pinDropdown(page: Page): Locator {
	return page.locator('div.fixed').filter({ hasText: 'Follow active tab' });
}

test.describe('desktop', () => {
	test('clicking a tab pins it, closes the dropdown, and updates the trigger label', async ({ page }) => {
		await loginAsOwner(page);
		const token = await ownerToken(page);
		await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);
		await openChat(page);

		const pinButton = page.locator('button[title="Pin to tab"], button[title^="Pinned:"]');
		await expect(pinButton).toBeVisible({ timeout: 15000 });
		await pinButton.click();

		const dropdown = pinDropdown(page);
		await expect(dropdown).toBeVisible({ timeout: 5000 });

		const tabItem = dropdown.getByText('Generation 1', { exact: true });
		await expect(tabItem).toBeVisible();
		await tabItem.click();

		// The menu itself must actually leave the DOM, not just become
		// visually hidden - this is the exact defect the repro caught.
		await expect(dropdown).toHaveCount(0);
		await expect(page.locator('button[title^="Pinned:"]')).toContainText('Generation 1');
		await expect(page.locator('button[title^="Pinned:"] span')).toHaveText('Generation 1');
	});

	test('clicking outside the dropdown closes it without pinning', async ({ page }) => {
		await loginAsOwner(page);
		const token = await ownerToken(page);
		await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);
		await openChat(page);

		const pinButton = page.locator('button[title="Pin to tab"], button[title^="Pinned:"]');
		await pinButton.click();
		const dropdown = pinDropdown(page);
		await expect(dropdown).toBeVisible({ timeout: 5000 });

		await page.locator('body').click({ position: { x: 20, y: 20 } });

		await expect(dropdown).toHaveCount(0);
		await expect(page.locator('button[title="Pin to tab"]')).toBeVisible();
	});

	test('Escape closes the dropdown but leaves the chat panel open', async ({ page }) => {
		await loginAsOwner(page);
		const token = await ownerToken(page);
		await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);
		const composer = await openChat(page);

		const pinButton = page.locator('button[title="Pin to tab"], button[title^="Pinned:"]');
		await pinButton.click();
		const dropdown = pinDropdown(page);
		await expect(dropdown).toBeVisible({ timeout: 5000 });

		await page.keyboard.press('Escape');

		await expect(dropdown).toHaveCount(0);
		await expect(composer).toBeVisible();
		await expect(page.locator('.fixed.top-0.right-0.bottom-0.z-50')).toBeVisible();
	});
});

test.describe('mobile', () => {
	test.use({ viewport: { width: 375, height: 812 }, hasTouch: true });

	test('tapping a dropdown item is not intercepted and pins/closes normally', async ({ page }) => {
		await loginAsOwner(page);
		const token = await ownerToken(page);
		await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);

		await page.goto('/generate');
		const panelStrip = page.getByRole('region', { name: 'Swipeable panels' });
		await expect(panelStrip).toBeVisible({ timeout: 15000 });
		await page.getByRole('button', { name: 'LLM' }).click();
		await page.waitForTimeout(400);

		const pinButton = page.locator('button[title="Pin to tab"], button[title^="Pinned:"]');
		await expect(pinButton).toBeVisible({ timeout: 15000 });
		await pinButton.click();

		const dropdown = pinDropdown(page);
		await expect(dropdown).toBeVisible({ timeout: 5000 });

		const tabItem = dropdown.getByText('Generation 1', { exact: true });
		// A real actionability-checked click: before the fix this timed out
		// with "intercepts pointer events" because the full-page catcher sat
		// above the menu inside the carousel's transformed stacking context.
		await tabItem.click({ timeout: 5000 });

		await expect(dropdown).toHaveCount(0);
	});
});
