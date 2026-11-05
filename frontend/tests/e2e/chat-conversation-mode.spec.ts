import { test, expect, type Request } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { installAndSelectImagePreset } from './presetPreamble';
import { startFakeLLM, seedFakeLlmConfig, type FakeLLMServer } from './fake-llm';

const JOURNEY = 'chat-conversation-mode';
const BACKEND = process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8055';
const REPLY = 'Castles were built for defence first and for show second.';

let fake: FakeLLMServer;

test.beforeEach(async () => {
	fake = await startFakeLLM();
});

test.afterEach(async () => {
	await fake.close();
});

function textOf(content: unknown): string {
	if (typeof content === 'string') return content;
	if (Array.isArray(content)) {
		return content.map((part) => (typeof part?.text === 'string' ? part.text : '')).join('\n');
	}
	return '';
}

test('Conversation mode talks to the model with no tools and nothing from the page', async ({ page }) => {
	test.setTimeout(120000);
	await loginAsOwner(page);
	const token = await ownerToken(page);
	await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);

	const preset = await installAndSelectImagePreset(page);
	if (!preset) await page.goto('/generate');

	const fab = page.getByRole('button', { name: 'AI Chat' });
	await expect(fab).toBeVisible({ timeout: 15000 });
	await fab.click();
	const composer = page.locator('.composer:has(button[aria-label="Send message"])');
	await expect(composer).toBeVisible({ timeout: 15000 });

	await page.getByRole('button', { name: 'New chat' }).first().click();
	const trigger = page.getByTestId('chat-mode-selector-trigger');
	await expect(trigger).toBeEnabled({ timeout: 15000 });
	await trigger.click();
	const menu = page.getByTestId('chat-mode-selector-menu');
	await expect(menu).toBeVisible();
	await menu.locator('button', { hasText: 'Conversation' }).click();
	await expect(trigger).toContainText('Conversation');

	await expect(page.getByText("What's on your mind?")).toBeVisible();
	await expect(composer.locator('button.composer-tool', { hasText: 'Tools' })).toHaveCount(0);
	await expect(composer.locator('button.composer-tool', { hasText: 'Memory' })).toHaveCount(0);
	await screenshot(page, JOURNEY, 'conversation-picked');

	fake.enqueue({ kind: 'text', text: REPLY });
	const sent: Promise<Request> = page.waitForRequest(
		(r) => /\/api\/chat\/sessions\/[^/]+\/messages\/stream$/.test(r.url()) && r.method() === 'POST'
	);
	const chipInput = composer.locator('[role="textbox"][aria-placeholder]');
	await chipInput.click();
	await page.keyboard.type('Why were castles built on hills?');
	await composer.locator('button[aria-label="Send message"]').click();

	const body = (await sent).postDataJSON() as { context_metadata?: Record<string, unknown> };
	expect(body.context_metadata?.form_state, 'no form state is sent').toBeUndefined();
	expect(body.context_metadata?.segments, 'no prompt segments are sent').toBeUndefined();

	await expect(page.getByText(REPLY)).toBeVisible({ timeout: 30000 });
	await expect(page.getByTestId('action-card')).toHaveCount(0);
	await expect(trigger).toBeDisabled();
	await expect(trigger).toContainText('Conversation');
	await expect(composer.locator('button.composer-tool', { hasText: 'Tools' })).toHaveCount(0);

	const turn = fake.requests.find((r) => r.servedAs === 'queued-turn');
	expect(turn, 'the conversation turn reached the model').toBeDefined();
	expect(turn!.toolCount, 'no tool is offered, not even a global one').toBe(0);
	const system = turn!.messages.find((m) => m.role === 'system');
	expect(textOf(system?.content)).toContain('open conversation');
	const user = [...turn!.messages].reverse().find((m) => m.role === 'user');
	const userText = textOf(user?.content);
	expect(userText).toContain('Why were castles built on hills?');
	expect(userText).not.toContain('<context>');
	expect(userText).not.toContain('PROMPT STATE');
	expect(userText).not.toContain('Generate workspace');
	await screenshot(page, JOURNEY, 'conversation-reply');
});
