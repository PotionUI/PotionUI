import { expect, type Page } from '@playwright/test';
import { ownerToken } from './helpers';

export const SWITCH = '[role="group"][aria-label="Prompt mode"]';

export async function assignMoss(page: Page): Promise<string | null> {
	const token = await ownerToken(page);
	const headers = { Authorization: `Bearer ${token}` };
	const list = await page.request.get('/api/presets?include_uninstalled=true', { headers });
	const presets = ((await list.json().catch(() => ({}))).data || []) as Array<{ id: string; name: string; installed?: boolean }>;
	const preset = presets.find((p) => /moss/i.test(p.name) || /moss/i.test(p.id));
	if (!preset) return null;
	if (!preset.installed) await page.request.post(`/api/presets/${preset.id}/install`, { headers });
	const me = await page.request.get('/api/auth/me', { headers });
	const userId = (await me.json())?.data?.id as string;
	await page.request.post(`/api/presets/${preset.id}/assign`, { headers, data: { user_ids: [userId] } });
	return preset.name;
}

export async function mockPresetVars(page: Page, withOption: boolean) {
	await page.route(/\/api\/presets\/[^/?]+(\?.*)?$/, async (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		const response = await route.fetch();
		const json = await response.json();
		const vars = { ...(json?.data?.vars ?? {}) };
		if (withOption) vars.regional_prompt = { modes: ['txt2img'], size_field: 'resolution' };
		else delete vars.regional_prompt;
		await route.fulfill({ response, json: { ...json, data: { ...json.data, vars } } });
	});
}

export async function openMoss(page: Page, name: string) {
	await page.goto('/generate');
	await page.waitForLoadState('networkidle');
	const phone = (page.viewportSize()?.width ?? 1440) < 768;
	if (phone) await page.getByRole('button', { name: 'Open preset and session' }).click();
	const scope = phone ? page.getByRole('dialog', { name: 'Preset and session' }) : page;
	const trigger = scope.locator('button[aria-haspopup="dialog"]', { hasText: 'Choose a preset' });
	await expect(trigger).toBeVisible({ timeout: 15000 });
	await trigger.click();
	const listbox = page.locator('[role="listbox"][aria-label="Presets"]');
	await expect(listbox).toBeVisible({ timeout: 15000 });
	await listbox.locator('[data-preset-family]').getByRole('option', { name, exact: true }).click();
	await expect(page.getByRole('dialog', { name: 'Choose preset' })).toBeHidden();
	if (phone) await page.keyboard.press('Escape');
}

export async function setTheme(page: Page, theme: 'dark' | 'light') {
	await page.evaluate((value) => document.documentElement.setAttribute('data-theme', value), theme);
	await page.waitForTimeout(300);
}

export async function centerCanvas(page: Page) {
	await page.getByTestId('regional-canvas').evaluate((el) => el.scrollIntoView({ block: 'center' }));
}

export async function drawRegion(page: Page, from: [number, number], to: [number, number]) {
	await centerCanvas(page);
	const box = (await page.getByTestId('regional-canvas').boundingBox())!;
	await page.mouse.move(box.x + box.width * from[0], box.y + box.height * from[1]);
	await page.mouse.down();
	await page.mouse.move(box.x + box.width * to[0], box.y + box.height * to[1], { steps: 8 });
	await page.mouse.up();
}

export async function fillCaption(page: Page, index: number, text: string) {
	await page.locator(`[data-card="${index}"] textarea`).fill(text);
}

export async function typePrompt(page: Page, text: string) {
	const editable = page.locator('.segment-composer [contenteditable="true"]').first();
	await editable.click();
	await page.keyboard.type(text);
}

export async function switchToRegional(page: Page) {
	const toggle = page.locator(SWITCH).getByRole('button', { name: /^Regional/ });
	await expect(toggle).toBeVisible({ timeout: 15000 });
	await toggle.click();
	await expect(page.getByTestId('regional-editor')).toBeVisible();
	await page.getByTestId('regional-canvas').scrollIntoViewIfNeeded();
}

