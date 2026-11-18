import type { Locator, Page } from '@playwright/test';

export async function openTool(page: Page, editor: Locator, id: string): Promise<void> {
	const direct = editor.locator(`[data-tool-rail] button[data-tool="${id}"]`);
	if (await direct.count()) {
		await direct.click();
		return;
	}
	await editor.locator(`[data-tool-rail] button[data-group-members~="${id}"]`).click();
	await page.locator(`[data-flyout-item="${id}"], [data-sheet-item="${id}"]`).click();
}

export function entry(editor: Locator, id: string): Locator {
	return editor.locator(`[data-entry-options="${id}"]`);
}

export async function setField(editor: Locator, id: string, name: string, value: string): Promise<void> {
	await entry(editor, id).locator(`[data-field-name="${name}"] input[type="range"]`).fill(value);
}
