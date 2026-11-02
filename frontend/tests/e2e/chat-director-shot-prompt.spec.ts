import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { assignPreset, startFromNoTabs } from './formSeedMocks';
import { selectPresetInPicker } from './presetPreamble';
import { startFakeLLM, seedFakeLlmConfig, type FakeLLMServer } from './fake-llm';

const JOURNEY = 'chat-director-shot-prompt';
const BACKEND = process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8055';
const OLD = 'a lighthouse at dusk';
const NEW = 'new shot prompt';
const TAG = `<tool_action type="update_director_segment" segment_index="0" segment_id="seg-1">${NEW}</tool_action>`;

let fake: FakeLLMServer;

test.beforeEach(async () => {
	fake = await startFakeLLM();
});

test.afterEach(async () => {
	await fake.close();
});

test.use({ viewport: { width: 1440, height: 960 } });

async function openPreset(page: Page, name: string, mode: RegExp | null) {
	await loginAsOwner(page);
	const token = await ownerToken(page);
	await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);
	const preset = await assignPreset(page, name);
	test.skip(!preset, `${name} preset is not available on this instance`);
	await page.goto('/generate');
	await startFromNoTabs(page);
	await page.goto('/generate');
	await page.waitForLoadState('networkidle');
	expect(await selectPresetInPicker(page, { id: preset!.id, name: preset!.name })).toBe(true);
	if (mode) {
		await page.getByTestId('preset-header-mode').locator('button').first().click();
		await page.getByRole('option').filter({ hasText: mode }).first().click();
		await expect(page.getByTestId('preset-header-mode')).toContainText(mode);
	}
}

function director(page: Page) {
	return page.locator('section.video-director[aria-label="Video Director"]').first();
}

function globalPromptButton(page: Page) {
	return page.getByRole('button', { name: /^Global prompt/ }).first();
}

async function applyShotVersion(page: Page, label: string) {
	await expect(director(page)).toBeVisible({ timeout: 20000 });
	const beat = director(page).locator('.beat-b').first();
	if ((await beat.count()) > 0) await beat.click();
	const shotPrompt = director(page).locator('.stage [contenteditable="true"]').first();
	await expect(shotPrompt).toBeVisible({ timeout: 20000 });
	await shotPrompt.click();
	await page.keyboard.type(OLD);
	await expect(shotPrompt).toContainText(OLD);
	await expect(globalPromptButton(page)).toHaveText(/^\s*Global prompt\s*$/);

	await page.getByRole('button', { name: 'AI Chat' }).click();
	const composer = page.locator('.composer:has(button[aria-label="Send message"])');
	await expect(composer).toBeVisible({ timeout: 15000 });
	fake.enqueue({ kind: 'text', text: `Here is a version of the shot:\n\n${TAG}` });
	const input = composer.locator('[role="textbox"][aria-placeholder]');
	await expect(input).toHaveAttribute('contenteditable', 'true');
	await input.click();
	await page.keyboard.type('Rewrite the shot prompt');
	await composer.locator('button[aria-label="Send message"]').click();

	const apply = page.locator('.assistant-message').last().locator('[data-testid="segment-action-apply"]');
	await expect(apply).toHaveCount(1, { timeout: 30000 });
	await apply.click();

	await expect(shotPrompt).toContainText(NEW);
	await expect(shotPrompt).not.toContainText(OLD);
	if ((await beat.count()) > 0) await expect(beat).toContainText(NEW);
	await expect(globalPromptButton(page)).toHaveText(/^\s*Global prompt\s*$/);
	await screenshot(page, JOURNEY, `${label}-applied-1440`);

	await page.getByRole('button', { name: 'Close chat', exact: true }).click();
	await expect(page.getByRole('dialog', { name: 'AI chat' })).toHaveCount(0);
	await globalPromptButton(page).click();
	const modal = page.getByRole('dialog').filter({ hasText: 'Applies to every shot in this film' });
	await expect(modal).toBeVisible();
	await expect(modal).not.toContainText(NEW);
}

test.describe('Chat Apply on a single-shot Video Director', () => {
	test('MiniMax-H3 References: the version lands in the shot Prompt, not the Global prompt', async ({ page }) => {
		test.setTimeout(180000);
		await openPreset(page, 'MiniMax-H3', /References/);
		await applyShotVersion(page, 'h3-refs');
	});

	test('LTX-2.5 video: the version lands in the shot Prompt, not the Global prompt', async ({ page }) => {
		test.setTimeout(180000);
		await openPreset(page, 'LTX-2.5', null);
		await applyShotVersion(page, 'ltx-video');
	});
});
