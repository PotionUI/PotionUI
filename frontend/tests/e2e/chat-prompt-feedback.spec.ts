import { test, expect } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { startFakeLLM, seedFakeLlmConfig, type FakeLLMServer } from './fake-llm';

const JOURNEY = 'chat-prompt-feedback';
const BEAT = 450;
const BACKEND = process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8055';

// Thumbs feedback on an LLM-proposed prompt: the assistant message carries a
// <tool_action type="update_segment"> block (the only shape the backend's
// feedback endpoint accepts as a candidate), the segment card renders thumbs,
// a click reflects pressed/disabled state immediately, and the verdict
// persists via metadata.prompt_feedback across a full page reload.

const PROPOSED_PROMPT = 'A serene mountain lake at dawn, mist rising over still water';

let fake: FakeLLMServer;

test.beforeEach(async () => {
	fake = await startFakeLLM();
});

test.afterEach(async () => {
	await fake.close();
});

test('thumbs-up on a proposed prompt reflects immediately and survives reload', async ({ page }) => {
	test.setTimeout(120000);
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

	fake.enqueue({
		kind: 'text',
		text:
			'Here is a stronger prompt for your first segment:\n' +
			`<tool_action type="update_segment" segment_index="0" segment_id="seg-e2e-1">${PROPOSED_PROMPT}</tool_action>`
	});

	const chipInput = composer.locator('[role="textbox"][aria-placeholder]');
	await chipInput.click();
	await page.keyboard.type('Improve my prompt please');
	await composer.locator('button[title="Send (Enter)"]').click();

	const segmentHeader = page.getByText('Update Segment #1');
	await expect(segmentHeader).toBeVisible({ timeout: 30000 });
	await expect(page.getByText(PROPOSED_PROMPT)).toBeVisible();

	const thumbsUp = page.locator('button[title="Good prompt"]');
	const thumbsDown = page.locator('button[title="Bad prompt"]');
	await expect(thumbsUp).toBeVisible();
	await expect(thumbsDown).toBeVisible();
	await expect(thumbsUp).toHaveAttribute('aria-pressed', 'false');

	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, 'proposal-with-thumbs');

	const feedbackResponse = page.waitForResponse(
		(r) => r.url().includes('/prompt-feedback') && r.request().method() === 'POST',
		{ timeout: 15000 }
	);
	await thumbsUp.click();

	// Pressed + disabled must reflect immediately (optimistic local state).
	await expect(thumbsUp).toHaveAttribute('aria-pressed', 'true');
	await expect(thumbsUp).toBeDisabled();
	await expect(thumbsDown).toBeDisabled();
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, 'thumbs-up-pressed');

	const response = await feedbackResponse;
	expect(response.ok(), `prompt-feedback -> ${response.status()}`).toBeTruthy();
	const responseJson = await response.json();
	expect(
		responseJson.success,
		`prompt-feedback must persist the verdict: ${JSON.stringify(responseJson)}`
	).toBeTruthy();

	// Reload: the session auto-restores for this mode and the persisted
	// metadata.prompt_feedback must drive the pressed state.
	await page.reload();
	await expect(fab).toBeVisible({ timeout: 15000 });
	await fab.click();
	await expect(page.getByText('Update Segment #1')).toBeVisible({ timeout: 30000 });

	const thumbsUpAfter = page.locator('button[title="Good prompt"]');
	await expect(thumbsUpAfter).toBeVisible();
	await expect(
		thumbsUpAfter,
		'thumbs-up state must persist across reload (metadata.prompt_feedback)'
	).toHaveAttribute('aria-pressed', 'true');
	await expect(thumbsUpAfter).toBeDisabled();

	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, 'after-reload-persisted');

	console.log(
		`[${JOURNEY}] proposal rendered with thumbs; thumbs-up pressed+disabled immediately; ` +
			`POST persisted (success=true); state still pressed after reload; ` +
			`fake-llm requests=${fake.requests.length}, unconsumed turns=${fake.pending()}`
	);
});
