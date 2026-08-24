import { test, expect } from '@playwright/test';
import { loginAsOwner, ownerToken, choosePreset, beat } from './helpers';
import { startFakeLLM, seedFakeLlmConfig, type FakeLLMServer } from './fake-llm';

// Scene 7: the AI Chat FAB, a prompt-help question, and the assistant's
// reply arriving with an actionable "Update Segment" card + Apply button.
// No real LLM is configured in this capture environment - the same
// OpenAI-compatible fake server frontend/tests/e2e/chat-*.spec.ts use
// (fake-llm.ts, duplicated here - see its header) stands in, wired through
// the real `POST /api/llm/configurations` + `/set-default` +
// `/user-assignments` admin API (seedFakeLlmConfig), so everything from the
// chat UI's point of view - streaming, tool_action parsing, the segment
// card - runs the real code path against a real HTTP server.
const BACKEND = process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8055';
const PROPOSED_PROMPT = 'A glowing potion bottle at golden hour, dramatic rim lighting, extreme macro detail';

let fake: FakeLLMServer;

test.beforeEach(async () => {
	fake = await startFakeLLM();
});

test.afterEach(async () => {
	await fake.close();
});

test('chat-assistant', async ({ page }) => {
	test.setTimeout(120000);
	await loginAsOwner(page);
	const token = await ownerToken(page);
	await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);

	await page.goto('/generate');
	await page.waitForLoadState('networkidle');
	await choosePreset(page, 'SDXL');
	await beat(page, 600);

	// A real positive segment must exist before Apply can land anywhere -
	// UnifiedAIChat's handleApplySegmentAction resolves the target segment
	// against tab.promptSegments and no-ops (idx === -1) against an empty
	// array, so the fake tool_action's Apply click would silently do nothing
	// without this.
	const promptEditor = page.locator('.inline-chip-editor[role="textbox"]').first();
	await expect(promptEditor).toBeVisible({ timeout: 15000 });
	await promptEditor.click();
	await page.keyboard.type('a glowing potion bottle on a wooden alchemist bench');
	await beat(page, 500);

	// Sidebar.svelte's AI Chat trigger has no `title` attribute - only its
	// accessible name, "AI Chat" (via aria-label), identifies it.
	const fab = page.getByRole('button', { name: 'AI Chat' });
	await expect(fab).toBeVisible({ timeout: 15000 });
	await fab.click();

	const composer = page.locator('.bg-surface-1.rounded-lg:has(button[title="Send (Enter)"])');
	await expect(composer).toBeVisible({ timeout: 15000 });
	await beat(page, 700);

	fake.enqueue({
		kind: 'text',
		text:
			'Here is a stronger version of your first segment:\n' +
			`<tool_action type="update_segment" segment_index="0" segment_id="seg-e2e-marketing">${PROPOSED_PROMPT}</tool_action>`
	});

	const chipInput = composer.locator('[role="textbox"][aria-placeholder]');
	await chipInput.click();
	await page.keyboard.type('Can you make my first segment more vivid?');
	await beat(page, 400);
	await composer.locator('button[title="Send (Enter)"]').click();
	await beat(page, 600);

	const segmentHeader = page.getByText('Update Segment #1');
	await expect(segmentHeader).toBeVisible({ timeout: 30000 });
	await expect(page.getByText(PROPOSED_PROMPT)).toBeVisible();
	await beat(page, 1200);

	const applyButton = page.getByRole('button', { name: 'Apply' });
	await expect(applyButton).toBeVisible();
	await applyButton.click();
	await expect(page.getByRole('button', { name: 'Applied' })).toBeVisible({ timeout: 10000 });
	await beat(page, 1500);
});
