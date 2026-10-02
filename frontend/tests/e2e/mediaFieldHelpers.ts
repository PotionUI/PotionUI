import { expect, type Locator, type Page } from '@playwright/test';

export type FieldToolId =
	| 'edit'
	| 'crop'
	| 'mask'
	| 'clear-mask'
	| 'trim'
	| 'frame'
	| 'split'
	| 'full'
	| 'earlier'
	| 'later'
	| 'replace'
	| 'remove'
	| 'remove-all';

export async function pickFieldTool(page: Page, field: Locator, id: FieldToolId) {
	await field.locator('[data-tools-trigger]').first().click();
	const item = page.locator(`[data-tools-menu] [data-tool="${id}"]`);
	await expect(item).toBeVisible();
	await item.click();
}

export function maskChip(field: Locator): Locator {
	return field.locator('[data-mask-chip]');
}

export async function pickFromSourceMenu(page: Page, scope: Locator, source: string) {
	await scope.getByRole('button', { name: 'More ways to add' }).click();
	const item = page.locator(`[data-source-menu] [data-source="${source}"]`);
	await expect(item).toBeVisible();
	await item.click();
}
