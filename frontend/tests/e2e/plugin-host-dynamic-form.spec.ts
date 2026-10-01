import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'plugin-host-dynamic-form';
const PRESET_ID = '4TK1KBQZ2XMB8ME0PTMXS1YJQP';

async function mountForm(page: Page, props: Record<string, unknown>) {
	await page.evaluate((props) => {
		const entry = (window as any).__potionui.components.DynamicForm;
		const el = document.body.appendChild(document.createElement('div'));
		el.style.cssText = 'position:fixed;top:80px;right:24px;width:380px;max-height:80vh;overflow:auto;z-index:60;padding:12px;';
		el.className = 'bg-surface-1 border border-line rounded-lg';
		const state: any = { entry, el, published: [] };
		state.handle = entry.mount(el, { ...props, onFormDataChange: (data: unknown) => state.published.push(data) });
		(window as any).__hostForm = state;
	}, props);
}

async function updateForm(page: Page, props: Record<string, unknown>) {
	await page.evaluate((props) => {
		const state = (window as any).__hostForm;
		state.entry.update(state.handle, props);
	}, props);
}

test('a preset form mounts through the plugin host outside Generate and follows its own audience', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await loginAsOwner(page);
	await page.goto('/history');
	await page.waitForFunction(() => !!(window as any).__potionui?.components?.DynamicForm);
	const stored = await page.evaluate(() => localStorage.getItem('potionui-form-audience'));
	expect(stored === null || stored === 'simple').toBeTruthy();

	await mountForm(page, {
		presetId: PRESET_ID,
		mode: 'txt2img',
		formName: 'Hosted preset form',
		initialData: { seed: 1234 },
		audience: 'simple'
	});

	const form = page.getByRole('region', { name: 'Hosted preset form' });
	await expect(form.getByRole('tab', { name: 'Generation' })).toBeVisible({ timeout: 30000 });
	await form.getByRole('tab', { name: 'Advanced' }).click();
	await expect(form.getByText('CFG Scale', { exact: true })).toHaveCount(0);
	await page.waitForFunction(() => {
		const published = (window as any).__hostForm.published;
		return published.length > 0 && published[published.length - 1].seed === 1234;
	});

	await updateForm(page, { audience: 'advanced' });
	await expect(form.getByText('CFG Scale', { exact: true })).toBeVisible();
	expect(await page.evaluate(() => localStorage.getItem('potionui-form-audience'))).toBe(stored);
	await screenshot(page, JOURNEY, 'advanced-1440');

	await updateForm(page, { audience: undefined });
	await expect(form.getByText('CFG Scale', { exact: true })).toHaveCount(0);

	await page.evaluate(() => {
		const state = (window as any).__hostForm;
		state.entry.unmount(state.handle);
		state.el.remove();
	});
	await expect(form).toHaveCount(0);
});
