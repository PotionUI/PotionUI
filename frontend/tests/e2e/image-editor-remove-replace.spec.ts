import { test, expect, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { selectRailTool } from './helpers';
import { begin, candidates, markArea, mark, matting, openHarbor, panel, prompt, qwen, row, taskEntry, update, type Json } from './imageEditorTaskMocks';

const SHOTS = process.env.E2E_SHOTS_DIR || join(process.cwd(), '..', 'design-proposals', 'editor-tasks');

async function shot(page: Page, name: string) {
	mkdirSync(SHOTS, { recursive: true });
	await page.evaluate(() => document.fonts.ready);
	await page.screenshot({ path: join(SHOTS, name), animations: 'disabled' });
}

const SUBJECT_CANDIDATE = { candidates: [{ patch: { item_id: 'subject-1' }, url: '/api/e2e-art/subject.png', seed: null }] };

test.describe('Remove an object', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('sends Include its shadow as an option, no prompt, and says the offer uses Edit a region', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region'), taskEntry('remove_object', 'Remove an object', { offers: [qwen({ via: 'edit_region' })] })]);
		await openHarbor(page);
		await selectRailTool(page, 'remove_object');
		await expect(prompt(page).locator('[data-prompt-field]')).toHaveCount(0);
		await expect(prompt(page).locator('[data-via-note]')).toHaveText('Uses Edit a region with a built-in instruction.');
		const shadow = panel(page).locator('[data-task-hint="include_shadow"]');
		await expect(shadow).not.toBeChecked();
		await expect(panel(page).getByText('Include its shadow')).toBeVisible();
		await markArea(page);
		await shadow.check();
		await shot(page, '30-remove-1440.png');
		await mark(page).click();
		await expect.poll(() => server.runs.length).toBe(1);
		expect(server.runs[0].task).toBe('remove_object');
		expect(server.runs[0].body.prompt).toBeNull();
		expect(server.runs[0].body.options).toEqual({ include_shadow: true });
		expect((server.runs[0].body.region as Json).item_id).toBeTruthy();
	});

	test('sends no options when the checkbox is off and shows no note for a direct offer', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region'), taskEntry('remove_object', 'Remove an object', { offers: [qwen()] })]);
		await openHarbor(page);
		await selectRailTool(page, 'remove_object');
		await expect(prompt(page).locator('[data-via-note]')).toHaveCount(0);
		await markArea(page);
		await mark(page).click();
		await expect.poll(() => server.runs.length).toBe(1);
		expect(server.runs[0].body).not.toHaveProperty('options');
		update(server, 'done', { candidates: candidates(1) });
		await expect(row(page, 's1')).toContainText('Remove an object');
	});

	test('keeps the shadow option for Try again', async ({ page }) => {
		const server = await begin(page, [taskEntry('edit_region', 'Edit a region'), taskEntry('remove_object', 'Remove an object', { offers: [qwen()] })]);
		await openHarbor(page);
		await selectRailTool(page, 'remove_object');
		await markArea(page);
		await panel(page).locator('[data-task-hint="include_shadow"]').check();
		await mark(page).click();
		await expect.poll(() => server.runs.length).toBe(1);
		update(server, 'done', { candidates: candidates(1) });
		await panel(page).locator('[data-task-hint="include_shadow"]').uncheck();
		await page.locator('[data-candidate-again]').click();
		await expect.poll(() => server.runs.length).toBe(2);
		expect(server.runs[1].body.options).toEqual({ include_shadow: true });
	});
});

test.describe('Replace the background', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	const tasks = () => [
		taskEntry('edit_region', 'Edit a region'),
		taskEntry('remove_object', 'Remove an object'),
		taskEntry('replace_background', 'Replace the background', { offers: [qwen({ via: 'edit_region' })] }),
		taskEntry('find_subject', 'Find the subject', { offers: [matting()] })
	];

	test('has a rail tool after Remove, and no tool for the internal Find the subject', async ({ page }) => {
		await begin(page, tasks());
		await openHarbor(page);
		const ids = await page.locator('[data-tool-rail] button[data-tool]').evaluateAll((buttons) => buttons.map((button) => button.getAttribute('data-tool')));
		expect(ids.indexOf('replace_background')).toBe(ids.indexOf('remove_object') + 1);
		expect(ids).not.toContain('find_subject');
	});

	test('finds the subject into the mask, then describes, colours, clears and pictures the background', async ({ page }) => {
		const server = await begin(page, tasks());
		server.immediate.find_subject = SUBJECT_CANDIDATE;
		await openHarbor(page);
		await selectRailTool(page, 'replace_background');
		await expect(panel(page).locator('[data-find-subject]')).toBeVisible();
		await expect(mark(page)).toBeDisabled();
		await expect(mark(page)).toHaveAccessibleName('Find the subject or mark the background first');
		await expect(page.locator('[data-mask-area]')).toHaveCount(0);

		await panel(page).locator('[data-find-subject]').click();
		await expect(panel(page).locator('[data-subject-done]')).toBeVisible();
		await expect(page.locator('[data-mask-area]')).toBeVisible();
		expect(server.runs[0].task).toBe('find_subject');
		expect(server.runs[0].body).toMatchObject({ region: null, prompt: null, setup: { preset_id: 'birefnet', mode: 'editor:matte' } });
		await expect(row(page, 's1')).toHaveCount(0);
		await shot(page, '31-replace-bg-1440.png');

		await prompt(page).locator('[data-prompt-field]').fill('a quiet beach at dusk');
		await mark(page).click();
		await expect.poll(() => server.runs.length).toBe(2);
		expect(server.runs[1].task).toBe('replace_background');
		expect(server.runs[1].body.prompt).toBe('a quiet beach at dusk');
		expect((server.runs[1].body.region as Json).item_id).toBeTruthy();
		update(server, 'done', { candidates: candidates(1) });
		await expect(row(page, 's1')).toContainText('Replace the background');
		await expect(row(page, 's1')).toContainText('1 of 1');

		const choice = panel(page).locator('[data-replace-background-choice]');
		await choice.getByRole('button', { name: 'Colour', exact: true }).click();
		await expect(prompt(page).locator('[data-prompt-field]')).toHaveCount(0);
		const hex = choice.locator('input[aria-label="Colour hex"]');
		await hex.fill('#336699');
		await hex.press('Enter');
		await panel(page).locator('[data-replace-background-apply]').click();
		await expect(page.locator('[data-edit-stack] [data-stack-row]').filter({ hasText: 'Replace background' }).filter({ hasText: '#336699' })).toHaveCount(1);

		await choice.getByRole('button', { name: 'Transparent', exact: true }).click();
		await panel(page).locator('[data-replace-background-apply]').click();
		await expect(page.locator('[data-edit-stack] [data-stack-row]').filter({ hasText: 'Replace background' }).filter({ hasText: 'Transparent' })).toHaveCount(1);

		await choice.getByRole('button', { name: 'Picture', exact: true }).click();
		await expect(panel(page).locator('[data-replace-background-apply]')).toBeDisabled();
		await expect(panel(page).locator('[data-replace-background-reason]')).toHaveText('Choose a picture first');
		await panel(page).locator('[data-background-choose]').click();
		await page.getByTestId('library-file-tile').first().locator('[data-pick-main]').click();
		await expect(panel(page).locator('[data-background-picture]')).toContainText('Sunset beach.png');
		await panel(page).locator('[data-replace-background-apply]').click();
		await expect(page.locator('[data-edit-stack] [data-stack-row]').filter({ hasText: 'Replace background' }).filter({ hasText: 'Sunset beach.png' })).toHaveCount(1);
		expect(server.runs).toHaveLength(2);

		await page.locator('[data-frame-save-menu]').click();
		await page.locator('[data-save-row="save-new"]').click();
		await expect.poll(() => server.mediaRuns.length).toBe(1);
		const steps = (server.mediaRuns[0].steps as Json[]).filter((step) => step.tool === 'replace_background');
		expect(steps).toHaveLength(3);
		expect(steps.map((step) => (step.params as Json).kind)).toEqual(['color', 'transparent', 'picture']);
		expect(steps[0].params).toMatchObject({ color: '#336699', matte: { item_id: expect.any(String) } });
		expect((steps[2].params as Json).picture).toEqual({ item_id: 'lib-bg-1' });
	});

	test('says the matting model is missing when nothing offers Find the subject', async ({ page }) => {
		await begin(page, [taskEntry('edit_region', 'Edit a region'), taskEntry('replace_background', 'Replace the background'), taskEntry('find_subject', 'Find the subject', { offers: [] })]);
		await openHarbor(page);
		await selectRailTool(page, 'replace_background');
		await panel(page).locator('[data-find-subject]').click();
		await expect(panel(page).locator('[data-subject-error]')).toContainText('Remove background');
		await expect(page.locator('[data-mask-area]')).toHaveCount(0);
	});

	test('shows a plain error when the subject search fails', async ({ page }) => {
		const server = await begin(page, tasks());
		await openHarbor(page);
		await selectRailTool(page, 'replace_background');
		await panel(page).locator('[data-find-subject]').click();
		await expect.poll(() => server.runs.length).toBe(1);
		await expect(panel(page).locator('[data-subject-progress]')).toBeVisible();
		update(server, 'failed', { error: 'CUDA out of memory' });
		await expect(panel(page).locator('[data-subject-error]')).toHaveText('The subject could not be found. Try again.');
		await expect(panel(page)).not.toContainText('CUDA');
	});

	test('marks the background by hand and replaces it without a subject search', async ({ page }) => {
		const server = await begin(page, tasks());
		await openHarbor(page);
		await selectRailTool(page, 'replace_background');
		await panel(page).locator('[data-replace-background-choice]').getByRole('button', { name: 'Transparent', exact: true }).click();
		await expect(panel(page).locator('[data-replace-background-apply]')).toBeDisabled();
		await expect(panel(page).locator('[data-replace-background-reason]')).toHaveText('Find the subject or mark the background first');
		await markArea(page);
		await expect(panel(page).locator('[data-replace-background-apply]')).toBeEnabled();
		await mark(page).click();
		await expect(page.locator('[data-edit-stack] [data-stack-row]').filter({ hasText: 'Transparent' })).toHaveCount(1);
		expect(server.runs).toHaveLength(0);
	});
});
