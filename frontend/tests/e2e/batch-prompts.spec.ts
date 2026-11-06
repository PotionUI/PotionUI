import { test, expect, type Page, type Locator } from '@playwright/test';
import { loginAsOwner, screenshot, addGenerationTab } from './helpers';
import { installAndSelectImagePreset, presetOption } from './presetPreamble';
import { makeModelFieldsOptional } from './generationMocks';

const JOURNEY = 'batch-prompts';

test.afterEach(async ({ page }) => {
	await page.unrouteAll({ behavior: 'ignoreErrors' });
});

async function setBatchCapability(page: Page, supported: boolean) {
	await page.route(/\/api\/presets(\?[^/]*)?$/, async (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		const response = await route.fetch();
		const body = await response.json();
		if (Array.isArray(body?.data)) {
			body.data = body.data.map((preset: Record<string, unknown>) => ({
				...preset,
				supports_prompt_batch: supported
			}));
		}
		await route.fulfill({ response, json: body });
	});
}

let presetName = '';

async function openGeneratePage(page: Page): Promise<boolean> {
	await loginAsOwner(page);
	const preset = await installAndSelectImagePreset(page);
	if (!preset) return false;
	presetName = preset.name;
	await page.setViewportSize({ width: 1440, height: 960 });
	await expect(positiveList(page)).toBeVisible({ timeout: 20000 });
	return true;
}

function positiveList(page: Page): Locator {
	return page.locator('div[role="list"][aria-label="Positive segments"]');
}

function positiveSegments(page: Page): Locator {
	return positiveList(page).locator('[role="listitem"]');
}

function promptTabs(page: Page): Locator {
	return page.getByRole('tablist', { name: 'Prompts' }).getByRole('tab');
}

function activePromptTab(page: Page): Locator {
	return page.getByRole('tablist', { name: 'Prompts' }).getByRole('tab', { selected: true });
}

function addPromptButton(page: Page): Locator {
	return page.locator('[data-header-lead]').getByRole('button', { name: 'Add prompt' });
}

async function typeIntoSegment(page: Page, index: number, text: string) {
	const editor = positiveSegments(page).nth(index).locator('.inline-chip-editor[role="textbox"]');
	await editor.click();
	await page.keyboard.type(text);
}

async function addPrompt(page: Page) {
	const before = await promptTabs(page).count();
	await addPromptButton(page).click();
	await expect(promptTabs(page)).toHaveCount(before + 1);
}

async function selectPrompt(page: Page, row: number) {
	await promptTabs(page).nth(row).click();
	await expect(promptTabs(page).nth(row)).toHaveAttribute('aria-selected', 'true');
}

async function selectPromptFromList(page: Page, row: number) {
	await page.getByRole('button', { name: 'All prompts' }).click();
	await page.getByRole('dialog', { name: 'Prompts' }).locator('[data-row-main]').nth(row).click();
	await expect(page.getByRole('dialog', { name: 'Prompts' })).toBeHidden();
}

async function setQuantity(page: Page, target: number) {
	await page.locator('[role="tablist"]:not([aria-label="Prompts"]) [role="tab"]').first().click();
	const label = page.getByText('Quantity', { exact: true }).first();
	await expect(label).toBeVisible({ timeout: 10000 });
	const card = label.locator('xpath=ancestor::*[contains(@class, "field-card")][1]');
	const slider = card.locator('input[type="range"]').first();
	await slider.fill(String(target));
	await expect(slider).toHaveValue(String(target));
}

async function authorTwoPrompts(page: Page) {
	await typeIntoSegment(page, 0, 'a lighthouse on a basalt cliff');
	await addPrompt(page);
	await expect(activePromptTab(page)).toHaveText('Prompt 2');
	await expect(positiveSegments(page).first()).toBeVisible();
	await typeIntoSegment(page, 0, 'red fox in autumn leaves');
	await page.locator('section.composer').first().getByRole('button', { name: 'Add segment' }).click();
	await expect(positiveSegments(page)).toHaveCount(2);
	await typeIntoSegment(page, 1, 'golden hour');
}

test('picker keeps every prompt and its segments when switching', async ({ page }) => {
	test.setTimeout(180000);
	await setBatchCapability(page, true);
	if (!(await openGeneratePage(page))) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return;
	}

	await expect(promptTabs(page)).toHaveCount(1);
	await authorTwoPrompts(page);
	await screenshot(page, JOURNEY, '00-two-prompts');

	await selectPrompt(page, 0);
	await expect(activePromptTab(page)).toHaveText('Prompt 1');
	await expect(positiveList(page)).toContainText('a lighthouse on a basalt cliff');
	await expect(positiveList(page)).not.toContainText('red fox');
	await expect(positiveSegments(page)).toHaveCount(1);

	await selectPromptFromList(page, 1);
	await expect(activePromptTab(page)).toHaveText('Prompt 2');
	await expect(positiveSegments(page)).toHaveCount(2);
	await expect(positiveList(page)).toContainText('red fox in autumn leaves');
	await expect(positiveList(page)).toContainText('golden hour');
});

test('generate sends every prompt with the batch flag and per-prompt state', async ({ page }) => {
	test.setTimeout(180000);
	await setBatchCapability(page, true);
	await makeModelFieldsOptional(page);
	if (!(await openGeneratePage(page))) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return;
	}
	await page.route('**/api/generations/start', (route) =>
		route.fulfill({ status: 400, json: { success: false, error: 'captured_by_spec', message: 'captured by spec' } })
	);

	await authorTwoPrompts(page);
	await expect(page.locator('[data-batch-line]')).toContainText('2 prompts × 1 image = 2 images');

	const request = page.waitForRequest(
		(candidate) => candidate.method() === 'POST' && candidate.url().endsWith('/api/generations/start')
	);
	await page.getByRole('button', { name: 'Generate 2 images', exact: true }).click();
	const sent = JSON.parse((await request).postData() ?? '{}');

	expect(sent.batch_prompts).toBe(true);
	expect(sent.prompts).toHaveLength(2);
	expect(sent.prompts[0].positive).toContain('lighthouse');
	expect(sent.prompts[1].positive).toContain('red fox');
	expect(sent.prompt_state.prompts).toHaveLength(2);
	expect(sent.prompt_state.prompts[1].promptSegments).toHaveLength(2);
	expect(sent.prompt_state.promptTabs).toBeUndefined();
});

test('count line follows quantity and blocks Generate above 20 images', async ({ page }) => {
	test.setTimeout(240000);
	await setBatchCapability(page, true);
	if (!(await openGeneratePage(page))) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return;
	}

	await typeIntoSegment(page, 0, 'first prompt');
	await addPrompt(page);
	await expect(page.locator('[data-batch-line]')).toContainText('2 prompts × 1 image = 2 images');

	for (let count = 3; count <= 7; count++) await addPrompt(page);
	await expect(promptTabs(page)).toHaveCount(7);
	await setQuantity(page, 3);
	await expect(page.locator('[data-batch-line]')).toContainText('7 prompts × 3 images = 21 images');
	await expect(page.getByText('Up to 20 images per run.').first()).toBeVisible();
	await expect(page.getByRole('button', { name: 'Up to 20 images per run.', exact: true })).toBeDisabled();
	await screenshot(page, JOURNEY, '01-over-cap');

	await page.getByRole('button', { name: 'Remove prompt 7' }).click();
	await expect(page.locator('[data-batch-line]')).toContainText('6 prompts × 3 images = 18 images');
	await expect(page.getByText('Up to 20 images per run.')).toHaveCount(0);
});

test('a saved session brings back every prompt and the current one', async ({ page }) => {
	test.setTimeout(240000);
	await setBatchCapability(page, true);
	if (!(await openGeneratePage(page))) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return;
	}

	await authorTwoPrompts(page);
	await selectPrompt(page, 0);

	const name = `batch-${Date.now()}`;
	await page.locator('button[aria-label="Save as a new session"]').click();
	const modal = page.locator('.fixed.inset-0');
	await expect(modal).toBeVisible();
	await modal.getByPlaceholder('Enter session name').fill(name);
	await Promise.all([
		page.waitForResponse((res) => res.url().includes('/api/sessions/save') && res.request().method() === 'POST'),
		modal.getByPlaceholder('Enter session name').press('Enter')
	]);
	await expect(modal).toBeHidden({ timeout: 10000 });
	await expect(page.getByTestId('session-trigger')).toContainText(name, { timeout: 10000 });

	await addGenerationTab(page);
	await expect(page.locator('.book-tab')).toHaveCount(2, { timeout: 10000 });
	await page.locator('button[aria-haspopup="dialog"]', { hasText: 'Choose a preset' }).click();
	await presetOption(page, presetName).click();
	await expect(page.getByRole('dialog', { name: 'Choose preset' })).toBeHidden();
	await expect(positiveList(page)).toBeVisible({ timeout: 20000 });
	await expect(promptTabs(page)).toHaveCount(1);

	await page.getByTestId('session-trigger').first().click();
	const drawer = page.getByRole('dialog', { name: 'Sessions' });
	await drawer.locator('[data-session-row]', { hasText: name }).first().click();

	await expect(positiveList(page)).toBeVisible({ timeout: 20000 });
	await expect(promptTabs(page)).toHaveCount(2, { timeout: 20000 });
	await expect(activePromptTab(page)).toHaveText('Prompt 1');
	await expect(positiveList(page)).toContainText('a lighthouse on a basalt cliff');

	await selectPrompt(page, 1);
	await expect(positiveSegments(page)).toHaveCount(2);
	await expect(positiveList(page)).toContainText('red fox in autumn leaves');
	await expect(positiveList(page)).toContainText('golden hour');
});

test('a preset that cannot batch locks Add prompt and explains why', async ({ page }) => {
	test.setTimeout(180000);
	await setBatchCapability(page, false);
	if (!(await openGeneratePage(page))) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return;
	}

	await expect(promptTabs(page)).toHaveCount(1);
	await expect(addPromptButton(page)).toBeDisabled();
	await addPromptButton(page).hover();
	await expect(page.getByRole('tooltip')).toContainText('This preset runs one prompt at a time.');
	await screenshot(page, JOURNEY, '02-unsupported');
});
