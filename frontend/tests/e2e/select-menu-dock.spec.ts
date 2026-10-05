import { test, expect, type Page, type Locator } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'select-menu-dock';
const PRESET_ID = '01M31Y6WNM07EHAXXCDV1QVJVX';
const SIZES = [
	{ tag: '1440', width: 1440, height: 900 },
	{ tag: '390', width: 390, height: 844 }
];

async function prepare(page: Page): Promise<string> {
	const token = await ownerToken(page);
	const headers = { Authorization: `Bearer ${token}` };
	const list = await page.request.get('/api/presets?include_uninstalled=true', { headers });
	const presets = ((await list.json()).data || []) as Array<{ id: string; name: string; installed?: boolean }>;
	const preset = presets.find((p) => p.id === PRESET_ID);
	test.skip(!preset, 'Qwen-Image-2.1 preset is not available on this instance');
	if (!preset!.installed) {
		const res = await page.request.post(`/api/presets/${PRESET_ID}/install`, { headers });
		expect(res.ok(), `install -> ${res.status()}`).toBeTruthy();
	}
	const me = await page.request.get('/api/auth/me', { headers });
	const userId = (await me.json()).data.id as string;
	const assign = await page.request.post(`/api/presets/${PRESET_ID}/assign`, { headers, data: { user_ids: [userId] } });
	expect(assign.ok(), `assign -> ${assign.status()}`).toBeTruthy();
	return preset!.name;
}

async function openControlTab(page: Page, mobile: boolean, name: string): Promise<Locator> {
	await page.goto('/generate');
	if (mobile) {
		await expect(page.locator('.studio-dock')).toBeVisible({ timeout: 15000 });
		await page.getByRole('button', { name: 'Open preset and session' }).click();
		const sheet = page.getByRole('dialog', { name: 'Preset and session' });
		await expect(sheet).toBeVisible();
		await sheet.locator('button[aria-haspopup="dialog"]').first().click();
		await page.getByText(name, { exact: true }).first().click();
		await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
		await sheet.getByTestId('preset-header-mode').getByRole('button').first().click();
		await page.getByRole('option', { name: /^Control/ }).first().click();
		await page.waitForTimeout(600);
		await page.keyboard.press('Escape');
		await page.waitForTimeout(400);
		if (!(await page.getByRole('dialog', { name: 'Settings' }).isVisible())) {
			await page.getByRole('button', { name: 'Settings' }).first().click();
		}
	} else {
		await page.getByRole('button', { name: 'Choose a preset' }).click();
		await page.getByText(name, { exact: true }).first().click();
		await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
		await page.getByTestId('preset-header-mode').getByRole('button').first().click();
		await page.getByRole('option', { name: /^Control/ }).first().click();
	}
	const root = mobile ? page.getByRole('dialog', { name: 'Settings' }) : page.locator('body');
	const tab = page.getByRole('tab', { name: 'Control', exact: true }).first();
	await expect(tab).toBeVisible({ timeout: 20000 });
	await tab.click();
	await expect(root.getByText('Guide', { exact: true }).first()).toBeVisible();
	await page.waitForTimeout(400);
	return root;
}


for (const size of SIZES) {
	test(`guide select menu clears the dock at ${size.tag}`, async ({ page }) => {
		const mobile = size.width < 600;
		await page.setViewportSize({ width: size.width, height: size.height });
		await loginAsOwner(page);
		const name = await prepare(page);
		const root = await openControlTab(page, mobile, name);

		const trigger = root.getByText('Guide', { exact: true }).first().locator('xpath=following::button[1]');
		await trigger.evaluate((el) => el.scrollIntoView({ block: 'end' }));
		if (!mobile) {
			await trigger.evaluate((el) => {
				let node: HTMLElement | null = el.parentElement;
				while (node && node.scrollHeight <= node.clientHeight + 1) node = node.parentElement;
				const target = window.innerHeight * 0.68;
				if (node) node.scrollBy(0, el.getBoundingClientRect().bottom - target);
			});
		}
		await page.waitForTimeout(300);
		if (!mobile) {
			const b = (await trigger.boundingBox())!;
			expect(b.y + b.height).toBeGreaterThan(size.height * 0.5);
			expect(b.y + b.height).toBeLessThan(size.height - 82 - 12 - 100);
		}
		await trigger.click();

		const menu = page.locator('[role="listbox"][data-dropdown="true"]');
		await expect(menu).toBeVisible();
		await page.waitForTimeout(300);

		const dockTop = await page.evaluate(() => {
			const dock = document.querySelector('.generation-panel');
			const rect = dock?.getBoundingClientRect();
			return rect && rect.height > 0 ? rect.top : window.innerHeight;
		});
		const dockCovers = !mobile;

		const options = menu.locator('[role="option"]');
		const count = await options.count();
		expect(count).toBeGreaterThanOrEqual(9);

		const box = (await menu.boundingBox())!;
		expect(box.y).toBeGreaterThanOrEqual(0);
		expect(box.y + box.height).toBeLessThanOrEqual(size.height);
		if (dockCovers) expect(box.y + box.height).toBeLessThanOrEqual(dockTop);
		await screenshot(page, JOURNEY, `01-open-${size.tag}`);

		const trigBox = (await trigger.boundingBox())!;
		const overlapsTrigger = box.y < trigBox.y + trigBox.height && box.y + box.height > trigBox.y;
		expect(overlapsTrigger).toBe(false);

		const last = options.nth(count - 1);
		for (let i = 0; i < count && (await last.getAttribute('data-active')) !== 'true'; i++) {
			await page.keyboard.press('ArrowDown');
			await page.waitForTimeout(60);
		}
		await page.waitForTimeout(200);
		await expect(last).toHaveAttribute('data-active', 'true');
		const lastBox = (await last.boundingBox())!;
		const menuBox = (await menu.boundingBox())!;
		expect(lastBox.y).toBeGreaterThanOrEqual(menuBox.y - 1);
		expect(lastBox.y + lastBox.height).toBeLessThanOrEqual(menuBox.y + menuBox.height + 1);
		expect(lastBox.y + lastBox.height).toBeLessThanOrEqual(size.height);
		if (dockCovers) expect(lastBox.y + lastBox.height).toBeLessThanOrEqual(dockTop);
		await screenshot(page, JOURNEY, `02-last-option-${size.tag}`);
	});
}
