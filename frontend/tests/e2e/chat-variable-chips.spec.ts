import { test, expect } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { installAndSelectImagePreset } from './presetPreamble';
import { startFakeLLM, seedFakeLlmConfig, type FakeLLMServer } from './fake-llm';

const JOURNEY = 'chat-variable-chips';
const BEAT = 450;
const BACKEND = process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8055';

// ${var} awareness in chat messages: a name defined in the generate tab's
// Variable Manager renders as a read-only chip (decorateVariableChips) inside
// assistant messages, with the variable's value as tooltip; unknown names stay
// literal text. Variables live only on the in-memory tab (not localStorage),
// so the journey defines one through the real Variable Manager UI on
// /generate — which requires an installed preset to render a prompt section —
// then drives the global chat panel against the fake LLM.

const VARIABLE_NAME = 'style';
const VARIABLE_VALUE = 'oil painting, warm light';

let fake: FakeLLMServer;

test.beforeEach(async () => {
	fake = await startFakeLLM();
});

test.afterEach(async () => {
	await fake.close();
});

test('known ${var} renders as a chip in chat, unknown stays literal', async ({ page }) => {
	test.setTimeout(120000);
	await loginAsOwner(page);
	const token = await ownerToken(page);
	const headers = { Authorization: `Bearer ${token}` };
	await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);

	const preset = await installAndSelectImagePreset(page);
	if (!preset) {
		test.skip(true, 'No native image preset available on this throwaway instance to render the prompt editor with.');
		return;
	}
	console.log(`[${JOURNEY}] selected preset ${preset.id} (${preset.name})`);

	// The prompt section (with the Variables entry point) needs preset + mode.
	const variablesButton = page.getByRole('button', { name: 'Variables' });
	try {
		await variablesButton.waitFor({ state: 'visible', timeout: 10000 });
	} catch {
		const modeButton = page.getByRole('button', { name: /txt2img/i }).first();
		if ((await modeButton.count()) > 0) {
			await modeButton.click();
		}
		await variablesButton.waitFor({ state: 'visible', timeout: 10000 });
	}
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, 'generate-with-preset');

	// Define a text variable through the real Variable Manager.
	await variablesButton.click();
	const modal = page.getByRole('dialog').filter({ hasText: 'Prompt variables' });
	await expect(modal).toBeVisible({ timeout: 10000 });
	await modal.getByLabel('Variable name').fill(VARIABLE_NAME);
	await modal.getByLabel('Variable value').fill(VARIABLE_VALUE);
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, 'variable-defined');
	await modal.getByRole('button', { name: 'Done' }).click();
	await expect(modal).toBeHidden();

	// Open the global chat panel (the 'AI Agent' text button this used to
	// click no longer exists — the /generate page shares the same
	// "AI Chat" trigger as everywhere else now).
	await page.getByRole('button', { name: 'AI Chat' }).click();
	const composer = page.locator('.bg-surface-1.rounded-lg:has(button[title="Send (Enter)"])');
	await expect(composer).toBeVisible({ timeout: 15000 });

	fake.enqueue({
		kind: 'text',
		text:
			'For the look, lean on ${style} in your prompt. ' +
			'Something like ${nope} is not one of your variables.'
	});

	const chipInput = composer.locator('[role="textbox"][aria-placeholder]');
	await chipInput.click();
	await page.keyboard.type('How should I use my variables?');
	await composer.locator('button[title="Send (Enter)"]').click();

	// Known ${style} becomes a chip carrying the variable's value as tooltip.
	const chip = page.locator(`span[title="${VARIABLE_VALUE}"]`);
	await expect(chip).toBeVisible({ timeout: 30000 });
	await expect(chip).toContainText(VARIABLE_NAME);

	// Unknown ${nope} stays literal, and the decorated ${style} literal is gone.
	await expect(page.getByText('${nope}')).toBeVisible();
	await expect(page.getByText('${style}')).toHaveCount(0);

	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, 'variable-chip-rendered');
	await chip.hover();
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, 'conversation-final');

	console.log(
		`[${JOURNEY}] variable '${VARIABLE_NAME}' defined via Variable Manager; assistant \${${VARIABLE_NAME}} ` +
			`rendered as chip with tooltip '${VARIABLE_VALUE}'; \${nope} stayed literal; ` +
			`fake-llm requests=${fake.requests.length}, unconsumed turns=${fake.pending()}`
	);
});
