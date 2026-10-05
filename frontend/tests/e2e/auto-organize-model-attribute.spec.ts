import { test, expect } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'auto-organize-model-attribute';
const RULE_NAME = 'E2E strong LoRAs';
const COLLECTION = 'E2E Strong LoRAs';

test('a model rule checks a model attribute with a typed value and reads back as a sentence', async ({ page }) => {
	test.setTimeout(120000);
	await page.setViewportSize({ width: 1440, height: 960 });
	await loginAsOwner(page);

	await page.goto('/auto-organize?subject=models&rule=new');
	const nameInput = page.getByLabel('Rule name');
	await expect(nameInput).toBeVisible({ timeout: 15000 });
	await nameInput.fill(RULE_NAME);

	await page.getByRole('button', { name: 'Add condition' }).click();
	const row = page.locator('[data-testid="condition-row"]').first();
	await row.locator('[aria-haspopup="listbox"]').first().click();
	await page.getByRole('option', { name: 'Attribute' }).click();

	const control = row.getByTestId('attribute-condition');
	await expect(control).toBeVisible();
	await control.getByTestId('attribute-picker').locator('[aria-haspopup="listbox"]').click();
	await page.getByRole('option', { name: /Recommended strength/ }).click();

	await control.getByTestId('attribute-operator').locator('[aria-haspopup="listbox"]').click();
	await page.getByRole('option', { name: 'is at least', exact: true }).click();
	const number = control.locator('[data-kind="number"] input[type="number"]');
	await expect(number).toBeVisible();
	await number.fill('0.8');
	await screenshot(page, JOURNEY, '1-attribute-condition');

	const actionRow = page.locator('[data-testid="action-row"][data-action="add_to_collection"]');
	await actionRow.getByRole('button').first().click();
	await page.getByText('Create a new collection').click();
	await actionRow.getByLabel('New collection name').fill(COLLECTION);

	await expect(page.getByTestId('preview-matched')).toBeVisible({ timeout: 15000 });
	await page.getByRole('button', { name: 'Create', exact: true }).click();

	await page.goto('/auto-organize?subject=models');
	const card = page.getByTestId('rule-card').filter({ hasText: RULE_NAME });
	await expect(card).toBeVisible({ timeout: 15000 });
	await expect(card).toContainText('Attribute Recommended strength is at least 0.8');
	await screenshot(page, JOURNEY, '2-rule-sentence');
});
