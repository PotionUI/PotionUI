import { test, expect, type Page, type Locator } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { startFakeLLM, seedFakeLlmConfig, type FakeLLMServer } from './fake-llm';

const JOURNEY = 'chat-chip-input-interactions';
const BEAT = 350;
const BACKEND = process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8055';

// The four ChatChipInput.svelte behaviors that genuinely need a
// browser — see resourceChipSegments.ts / chatResourceSuggestions.ts for the
// pure-logic coverage this complements. Deliberately stays on a FRESH
// instance (no installed/assigned preset): the global chat FAB is always
// present, and a resource chip can be attached through the always-registered
// root '@' namespace listing + the client-side '@form.' branch
// (buildFormSuggestions, chatResourceSuggestions.ts) — neither needs
// models/tests depot fixtures or a selected preset.
//
// It DOES need an enabled LLM config, though: UnifiedAIChat.svelte wires
// `disabled={isGenerating || llmConfigs.length === 0}` into ChatChipInput, so
// on a truly fresh instance (no LLM configured) the whole editor renders
// contenteditable="false" and nothing below is reachable — confirmed by a
// real run, not assumed. Same fake-LLM-seed pattern as
// chat-tool-approval.spec.ts / chat-prompt-feedback.spec.ts.

let fake: FakeLLMServer;

test.beforeEach(async () => {
	fake = await startFakeLLM();
});

test.afterEach(async () => {
	await fake.close();
});

async function openGlobalChat(page: Page): Promise<Locator> {
	await loginAsOwner(page);
	const token = await ownerToken(page);
	await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);

	await page.goto('/models');
	// Sidebar.svelte's AI Chat trigger has no `title` attribute (its tooltip
	// text comes from a wrapping <Tooltip>, not a native title) — only its
	// accessible name, "AI Chat" (via aria-label), identifies it.
	const fab = page.getByRole('button', { name: 'AI Chat' });
	await expect(fab).toBeVisible({ timeout: 15000 });
	await fab.click();
	const composer = page.locator('.bg-surface-1.rounded-lg:has(button[title="Send (Enter)"])');
	await expect(composer).toBeVisible({ timeout: 15000 });
	const chipInput = composer.locator('.chat-chip-input[role="textbox"]');
	await expect(chipInput).toBeVisible();
	await expect(chipInput).toHaveAttribute('contenteditable', 'true');
	return chipInput;
}

/** Navigates into the always-present '@form.' namespace (client-side only,
 *  see chatResourceSuggestions.ts buildFormSuggestions — no network call, no
 *  depot/preset dependency) and attaches whatever its first suggestion is.
 *  Deliberately doesn't assert WHICH suggestion (formData on a fresh, non-
 *  /generate context is uncertain — could be `{}` or a stale pinned tab's
 *  values) — only that the attach mechanic itself works. */
async function attachFirstFormSuggestion(page: Page, chipInput: Locator): Promise<void> {
	await chipInput.click();
	await page.keyboard.type('@form.');
	const firstOption = page.locator('[role="option"]').first();
	await expect(firstOption).toBeVisible({ timeout: 5000 });
	await page.keyboard.press('Enter');
	await expect(chipInput.locator('.inline-chip-container')).toHaveCount(1);
}

test.describe('ChatChipInput browser interactions', () => {
	test('paste inserts plain text at the cursor and leaves the caret after it', async ({ page }) => {
		test.setTimeout(120000);
		const chipInput = await openGlobalChat(page);
		await chipInput.click();
		await page.keyboard.type('before ');

		await chipInput.evaluate((el) => {
			const dt = new DataTransfer();
			dt.setData('text/plain', 'PASTED');
			const evt = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
			el.dispatchEvent(evt);
		});
		await page.keyboard.type('X');

		await expect(chipInput).toHaveText('before PASTEDX');
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'paste-inserted-at-cursor');
	});

	test('the @ suggest dropdown supports Arrow/Enter keyboard navigation over root namespaces', async ({
		page
	}) => {
		test.setTimeout(120000);
		const chipInput = await openGlobalChat(page);
		await chipInput.click();
		await page.keyboard.type('@');

		// Root '@' (empty query) lists every registered resource-provider
		// namespace (registry.py suggest(): "an empty or dot-free query lists
		// namespaces") — structurally always >= 2 on any instance (form,
		// generations, models, presets, phrasebook are all builtin), so this
		// doesn't depend on depot/preset fixtures. Exact count/order isn't
		// asserted (plugins can register more namespaces).
		const options = page.locator('[role="option"]');
		await expect(options.nth(1)).toBeVisible({ timeout: 5000 });
		const count = await options.count();
		expect(count).toBeGreaterThanOrEqual(2);

		await expect(options.nth(0)).toHaveAttribute('aria-selected', 'true');
		await expect(options.nth(1)).toHaveAttribute('aria-selected', 'false');
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'suggest-dropdown-open');

		await page.keyboard.press('ArrowDown');
		await expect(options.nth(0)).toHaveAttribute('aria-selected', 'false');
		await expect(options.nth(1)).toHaveAttribute('aria-selected', 'true');
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'suggest-dropdown-arrow-down');

		await page.keyboard.press('ArrowUp');
		await expect(options.nth(0)).toHaveAttribute('aria-selected', 'true');

		// Enter on a root namespace (has_children, not attachable -> 'browse'
		// per resolveMentionRowAction) navigates INTO it rather than attaching —
		// the editor text grows to "@<namespace>." and the dropdown stays open
		// showing that namespace's own suggestions.
		const firstLabel = (await options.nth(0).textContent()) ?? '';
		await page.keyboard.press('Enter');
		const text = (await chipInput.textContent()) ?? '';
		expect(text.startsWith('@'), `expected editor text to still start with '@', got ${JSON.stringify(text)}`).toBe(
			true
		);
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'suggest-dropdown-navigated-into-namespace');
		console.log(`[${JOURNEY}] navigated into root namespace option labeled ${JSON.stringify(firstLabel)}`);
	});

	test('Backspace immediately after an attached resource chip removes it', async ({ page }) => {
		test.setTimeout(120000);
		const chipInput = await openGlobalChat(page);
		await attachFirstFormSuggestion(page, chipInput);
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'chip-attached-before-backspace');

		// attachResource() leaves the caret right after the newly-inserted chip
		// (its own range.setStartAfter(chipContainer)) — the same DOM-boundary
		// caret position InlineChipEditor's chipifyNewTokens produces.
		await page.keyboard.press('Backspace');

		await expect(chipInput.locator('.inline-chip-container')).toHaveCount(0);
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'chip-removed-after-backspace');
	});

	// GAP (documented, not endorsed): grep confirms no 'Delete' branch in
	// ChatChipInput's handleKeyDown either. Same caveat as the InlineChipEditor
	// spec's equivalent test: this is an INFORMED PREDICTION of Chromium's
	// native atomic-void forward-delete behavior (mirroring what Backspace
	// relies on natively before this app's handler intercepts it), UNVERIFIED
	// until this spec runs. There is a second, compounding uncertainty here
	// specific to this construction: typing a character while the caret sits
	// at a DOM-boundary position immediately after an atomic chip (rather
	// than inside a text node) is itself unverified browser behavior — if
	// that step behaves differently than assumed, the whole scenario's setup
	// is invalid, not just the Delete assertion.
	test('Delete immediately before an attached resource chip (forward-delete gap, unhandled)', async ({
		page
	}) => {
		test.setTimeout(120000);
		const chipInput = await openGlobalChat(page);
		await attachFirstFormSuggestion(page, chipInput);
		await page.keyboard.type('c');
		await page.keyboard.press('ArrowLeft');
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'chip-before-delete');

		await page.keyboard.press('Delete');
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'chip-after-delete');

		// PREDICTION: native atomic-void deletion removes the whole chip.
		await expect(chipInput.locator('.inline-chip-container')).toHaveCount(0);
		await expect(chipInput).toHaveText('c');
	});

	test('a resource chip mounts on attach and unmounts on removal', async ({ page }) => {
		test.setTimeout(120000);
		const chipInput = await openGlobalChat(page);
		await attachFirstFormSuggestion(page, chipInput);

		// mount(): ResourceChip renders with its '@' + label + remove button.
		const chip = chipInput.locator('.inline-chip').first();
		await expect(chip).toBeVisible();
		await expect(chip.locator('button[title="Remove resource"]')).toBeVisible();
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'resource-chip-mounted');

		// unmount(): ChatChipInput has no InlineChipEditor-style
		// getChipsHash/remount-on-external-props reactive block (confirmed by
		// inventory — its only external-sync path is the whole-value
		// syncDOMWithValue rebuild), so there is no analogous "remount on stale
		// props" scenario to exercise here; this only covers mount + unmount.
		await chip.locator('button[title="Remove resource"]').click();
		await expect(chipInput.locator('.inline-chip-container')).toHaveCount(0);
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'resource-chip-unmounted');
	});
});
