import { test, expect, type Locator, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot, shotPath } from './helpers';

const JOURNEY = 'focus-treatment';
const SDXL_PRESET_ID = '01K0W24A3RADXXABH16YQ7KE90';

async function css(el: Locator, prop: 'outlineStyle' | 'boxShadow' | 'outlineColor' | 'borderBottomColor'): Promise<string> {
	return el.evaluate((node, p) => getComputedStyle(node)[p as 'outlineStyle'], prop);
}

async function signalColor(page: Page): Promise<string> {
	return page.evaluate(() => {
		const probe = document.createElement('div');
		probe.style.color = 'rgb(var(--signal))';
		document.body.appendChild(probe);
		const value = getComputedStyle(probe).color;
		probe.remove();
		return value;
	});
}

function ringColor(boxShadow: string): string | null {
	const ring = boxShadow.split(/,(?![^(]*\))/).find((layer) => /\s2px\b/.test(layer));
	const match = ring?.match(/rgba?\([^)]*\)/);
	return match ? match[0] : null;
}

async function expectRing(el: Locator, signal: string) {
	await expect.poll(() => css(el, 'boxShadow'), { timeout: 5000 }).not.toBe('none');
	await expect.poll(async () => ringColor(await css(el, 'boxShadow')), { timeout: 5000 }).toBe(signal);
}

async function expectNoRing(el: Locator) {
	expect(await css(el, 'boxShadow')).toBe('none');
	expect(await css(el, 'outlineStyle')).toBe('none');
}

async function keyboardFocus(page: Page, el: Locator) {
	await page.keyboard.press('Shift');
	await el.focus();
	await expect(el).toBeFocused();
	await page.keyboard.press('Shift');
	await expect(el).toBeFocused();
	await expect.poll(() => el.evaluate((node) => node.matches(':focus-visible')), { timeout: 5000 }).toBe(true);
}

async function tabTo(page: Page, el: Locator) {
	await page.locator('#fx-start').click();
	const id = await el.getAttribute('id');
	for (let hops = 0; hops < 10; hops++) {
		await page.keyboard.press('Tab');
		if ((await page.evaluate(() => document.activeElement?.id)) === id) break;
	}
	await expect(el).toBeFocused();
	await expect.poll(() => el.evaluate((node) => node.matches(':focus-visible')), { timeout: 5000 }).toBe(true);
}

async function shoot(el: Locator, label: string) {
	await el.scrollIntoViewIfNeeded();
	await el.screenshot({ path: shotPath(JOURNEY, label) });
}

async function apiJson(page: Page, method: 'get' | 'post', url: string, token: string, data?: unknown) {
	const res = await page.request[method](url, { headers: { Authorization: `Bearer ${token}` }, data: data ?? {} });
	expect(res.ok(), `${method} ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

test('filter bar search: one signal ring on the group, none on the inner input', async ({ page }) => {
	await loginAsOwner(page);
	const signal = await signalColor(page);
	await page.goto('/admin?tab=models');
	const search = page.getByPlaceholder(/Search by filename/).first();
	await expect(search).toBeVisible({ timeout: 15000 });
	const group = search.locator('xpath=ancestor::div[contains(@class,"input")][1]');
	await page.mouse.click(1, 1);
	expect(await css(group, 'boxShadow')).toBe('none');
	await keyboardFocus(page, search);
	await shoot(group, 'filter-bar-search-focus');
	await expectNoRing(search);
	await expectRing(group, signal);
});

test('bare and .input controls carry one signal ring on themselves', async ({ page }) => {
	await loginAsOwner(page);
	const signal = await signalColor(page);
	await page.goto('/admin?tab=models');
	await page.evaluate(() => {
		const host = document.createElement('div');
		host.id = 'focus-fixture';
		host.style.cssText =
			'position:fixed;top:80px;left:40px;width:380px;z-index:9999;display:flex;flex-direction:column;gap:16px;padding:16px;background:rgb(var(--surface-1))';
		host.innerHTML =
			'<button id="fx-start" type="button">Start</button>' +
			'<input id="fx-bare" type="text" value="Bare input" />' +
			'<textarea id="fx-bare-area" rows="2">Bare textarea</textarea>' +
			'<input id="fx-text" class="input" type="text" value="Text input" />' +
			'<textarea id="fx-area" class="input" rows="3">Textarea</textarea>' +
			'<select id="fx-select" class="input"><option>One</option><option>Two</option></select>';
		document.body.appendChild(host);
	});
	for (const id of ['#fx-bare', '#fx-bare-area', '#fx-text', '#fx-area', '#fx-select']) {
		const el = page.locator(id);
		await tabTo(page, el);
		await shoot(page.locator('#focus-fixture'), `fixture-${id.slice(4)}-focus`);
		expect(await css(el, 'outlineStyle')).toBe('none');
		await expectRing(el, signal);
	}
});

test('underline login inputs keep their own border treatment', async ({ browser }) => {
	const page = await browser.newPage();
	await page.goto('/login');
	const signal = await signalColor(page);
	const username = page.locator('#username');
	await expect(username).toBeVisible({ timeout: 15000 });
	await keyboardFocus(page, username);
	await page.screenshot({ path: shotPath(JOURNEY, 'login-focus') });
	expect(await css(username, 'boxShadow')).toBe('none');
	expect(await css(username, 'outlineColor')).toBe('rgba(0, 0, 0, 0)');
	await expect.poll(() => css(username, 'borderBottomColor'), { timeout: 5000 }).toBe(signal);
	await page.close();
});

test('preset form frames and the prompt segment editor', async ({ page }) => {
	await page.setViewportSize({ width: 1400, height: 950 });
	await loginAsOwner(page);
	const token = await ownerToken(page);
	const signal = await signalColor(page);
	const me = await apiJson(page, 'get', '/api/auth/me', token);
	const list = await apiJson(page, 'get', '/api/presets?include_uninstalled=true', token);
	const presets = (list.data || []) as Array<{ id: string; name: string; installed?: boolean }>;
	const preset =
		presets.find((p) => p.id === SDXL_PRESET_ID) || presets.find((p) => /sdxl/i.test(p.name)) || presets.find((p) => /flux/i.test(p.name));
	expect(preset, 'a preset with a seed field must exist').toBeTruthy();
	if (!preset!.installed) await apiJson(page, 'post', `/api/presets/${preset!.id}/install`, token);
	await apiJson(page, 'post', `/api/presets/${preset!.id}/assign`, token, { user_ids: [me.data.id] });

	await page.goto('/generate');
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByText(preset!.name, { exact: true }).first().click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
	const generationTab = page.getByRole('tab', { name: 'Generation' });
	if ((await generationTab.count()) > 0) await generationTab.click();

	const inner = page.locator('.field-frame input[type="number"]').first();
	await expect(inner).toBeVisible({ timeout: 20000 });
	const frame = inner.locator('xpath=ancestor::div[contains(@class,"field-frame")][1]');
	await keyboardFocus(page, inner);
	await shoot(frame, 'seed-field-focus');
	await expectNoRing(inner);
	await expectRing(frame, signal);

	const button = frame.locator('button').first();
	await page.keyboard.press('Tab');
	await button.focus();
	expect(await css(frame, 'boxShadow')).toBe('none');

	const editor = page.locator('.inline-chip-editor[role="textbox"]').first();
	await expect(editor).toBeVisible({ timeout: 15000 });
	await editor.click();
	await page.keyboard.type('/');
	const browseAll = page.getByRole('button', { name: /Browse all in/ }).first();
	await expect(browseAll).toBeVisible({ timeout: 10000 });
	await browseAll.click();
	const modalSearch = page.locator('.picker-modal-search');
	await expect(modalSearch).toBeVisible({ timeout: 10000 });
	const modalInput = modalSearch.locator('input');
	await modalInput.click();
	await page.waitForTimeout(250);
	await page.screenshot({ path: shotPath(JOURNEY, 'picker-modal-search-focus') });
	await expectNoRing(modalInput);
	await expectRing(modalSearch, signal);
	await screenshot(page, JOURNEY, 'page');
});
