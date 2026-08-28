import { test, expect, type Page, type Locator } from '@playwright/test';
import { loginAsOwner, ownerToken } from './helpers';
import { startFakeLLM, seedFakeLlmConfig, type FakeLLMServer } from './fake-llm';

// Regression coverage for the tab-pin UI. The pin picker (portaled menu +
// computed fixed position + document-level close listeners, see
// ChatContextStrip.svelte) used to live in ChatInput.svelte's composer
// toolbar; it moved into ChatContextStrip (the tab name is now the trigger)
// when the strip took over tab naming, and the composer's own pin button was
// reduced to a plain pin/unpin toggle for whichever tab the strip is
// currently reading. This spec covers both halves. The dropdown itself used
// to build a portaled full-page click-catcher next to a non-portaled menu: on
// desktop the catcher unmounted on close but the menu stayed in the DOM
// forever, and on mobile (chat embedded in the generate page's transformed
// swipe carousel) the catcher painted above the menu and absorbed every tap —
// that regression risk now lives at the strip's anchor instead, so the tests
// below target it there. Requires an enabled LLM config to exist, or
// UnifiedAIChat renders its "No enabled LLM configurations" empty state
// instead of the composer.

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

function pickerDropdown(page: Page): Locator {
	return page.locator('[data-testid="chat-context-strip-picker"]');
}

function stripTrigger(page: Page): Locator {
	// The strip's tab name is the picker trigger, in every state.
	return page.locator('[data-testid="chat-context-strip"] button').first();
}

test.describe('desktop', () => {
	test('composer pin button toggles pin/unpin for the tab the strip is reading', async ({ page }) => {
		await loginAsOwner(page);
		const token = await ownerToken(page);
		await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);
		await openChat(page);

		const pinToggle = page.locator('button[title^="Pin to "], button[title^="Unpin from "]');
		await expect(pinToggle).toBeVisible({ timeout: 15000 });
		await expect(pinToggle).toHaveAttribute('title', 'Pin to Generation 1');
		await expect(pinToggle).toHaveAttribute('aria-pressed', 'false');

		await pinToggle.click();
		await expect(pinToggle).toHaveAttribute('title', 'Unpin from Generation 1');
		await expect(pinToggle).toHaveAttribute('aria-pressed', 'true');
		await expect(page.locator('[data-testid="chat-context-strip"]')).toHaveAttribute(
			'data-strip-state',
			'pinned-active'
		);

		await pinToggle.click();
		await expect(pinToggle).toHaveAttribute('title', 'Pin to Generation 1');
		await expect(pinToggle).toHaveAttribute('aria-pressed', 'false');
		await expect(page.locator('[data-testid="chat-context-strip"]')).toHaveAttribute(
			'data-strip-state',
			'following'
		);
	});

	test('clicking the strip tab name opens the picker; clicking a tab pins it and closes the dropdown', async ({
		page
	}) => {
		await loginAsOwner(page);
		const token = await ownerToken(page);
		await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);
		await openChat(page);

		await stripTrigger(page).click();
		const dropdown = pickerDropdown(page);
		await expect(dropdown).toBeVisible({ timeout: 5000 });
		await expect(dropdown).toContainText('Follow active tab');

		const tabItem = dropdown.getByText('Generation 1', { exact: true });
		await expect(tabItem).toBeVisible();
		await tabItem.click();

		// The menu itself must actually leave the DOM, not just become
		// visually hidden - this is the exact defect the repro caught.
		await expect(dropdown).toHaveCount(0);
		await expect(page.locator('[data-testid="chat-context-strip"]')).toHaveAttribute(
			'data-strip-state',
			'pinned-active'
		);
	});

	test('clicking outside the dropdown closes it without pinning', async ({ page }) => {
		await loginAsOwner(page);
		const token = await ownerToken(page);
		await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);
		await openChat(page);

		await stripTrigger(page).click();
		const dropdown = pickerDropdown(page);
		await expect(dropdown).toBeVisible({ timeout: 5000 });

		await page.locator('body').click({ position: { x: 20, y: 20 } });

		await expect(dropdown).toHaveCount(0);
		await expect(page.locator('[data-testid="chat-context-strip"]')).toHaveAttribute(
			'data-strip-state',
			'following'
		);
	});

	test('Escape closes the dropdown but leaves the chat panel open', async ({ page }) => {
		await loginAsOwner(page);
		const token = await ownerToken(page);
		await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);
		const composer = await openChat(page);

		await stripTrigger(page).click();
		const dropdown = pickerDropdown(page);
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

		await stripTrigger(page).click();
		const dropdown = pickerDropdown(page);
		await expect(dropdown).toBeVisible({ timeout: 5000 });

		const tabItem = dropdown.getByText('Generation 1', { exact: true });
		// A real actionability-checked click: before the original fix this
		// timed out with "intercepts pointer events" because a full-page
		// catcher sat above the menu inside the carousel's transformed
		// stacking context. Same regression class, now checked at the strip's
		// anchor instead of the composer's.
		await tabItem.click({ timeout: 5000 });

		await expect(dropdown).toHaveCount(0);
	});
});
