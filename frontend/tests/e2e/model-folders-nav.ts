import { expect, type Page } from '@playwright/test';

export const FOLDERS_URL = '/admin?tab=models&view=folders';

export function sidebarRow(page: Page, name: string | RegExp) {
	const pattern = typeof name === 'string' ? new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`) : name;
	return page.getByRole('option', { name: pattern });
}

export async function gotoFolders(page: Page, view?: string | RegExp) {
	await page.goto(FOLDERS_URL);
	await expect(page.locator('[data-folders-pane]')).toBeVisible({ timeout: 15000 });
	await expect(page.locator('[data-folders-pane]').getByRole('heading', { level: 2 })).toBeVisible({ timeout: 15000 });
	if (view) await selectFolderView(page, view);
}

export async function selectFolderView(page: Page, view: string | RegExp) {
	const phone = (page.viewportSize()?.width ?? 1440) < 768;
	if (phone) {
		const select = page.getByLabel('Folder', { exact: true });
		const find = (options: string[]) => options.find((o) => (typeof view === 'string' ? o.startsWith(view) : view.test(o)));
		await expect
			.poll(async () => find(await select.locator('option').allTextContents()) ?? '', {
				message: `no Folder option for ${String(view)}`,
				timeout: 15000
			})
			.not.toBe('');
		const match = find(await select.locator('option').allTextContents())!;
		await select.selectOption({ label: match });
		return;
	}
	await sidebarRow(page, view).click();
}

export function typeRow(page: Page, type: string) {
	return page.locator('[data-binding-row]').filter({ hasText: type });
}

export async function openRowMenu(page: Page, subdirOrType: string, item: string) {
	await page.getByRole('button', { name: `Actions for ${subdirOrType}`, exact: true }).click();
	await page.getByRole('menuitem', { name: item }).click();
}

export async function clickAddFolder(page: Page) {
	const phone = (page.viewportSize()?.width ?? 1440) < 768;
	if (phone) await page.getByRole('button', { name: 'Add folder' }).first().click();
	else await sidebarRow(page, 'Add folder').click();
}

export async function openFolderSettings(page: Page) {
	await page.getByRole('button', { name: 'Folder settings' }).click();
	await expect(page.locator('[data-side-panel]')).toBeVisible();
}
