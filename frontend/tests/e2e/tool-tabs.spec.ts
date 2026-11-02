import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, scopedStorageKey, screenshot } from './helpers';

const JOURNEY = 'tool-tabs';
const PLUGIN_ID = 'tool-tab-fixture';
const STAMP_MODULE = `
export default class StampToolTab {
	constructor({ target, props }) {
		this.host = props.host;
		this.el = document.createElement('div');
		this.el.setAttribute('data-testid', 'stamp-tool');
		this.el.style.padding = '24px';
		this.el.style.color = 'rgb(var(--fg))';
		this.label = document.createElement('p');
		this.button = document.createElement('button');
		this.button.type = 'button';
		this.button.textContent = 'Stamp twice';
		this.button.addEventListener('click', () => {
			this.host.setState({ stamps: 2 });
			this.host.setDirty(2);
		});
		this.el.append(this.label, this.button);
		target.appendChild(this.el);
		this.render();
	}
	render() {
		this.label.textContent = 'Stamp tool on ' + this.host.tabId + ' stamps ' + ((this.host.state && this.host.state.stamps) || 0);
	}
	$set(props) {
		if (props.host) this.host = props.host;
		this.render();
	}
	$destroy() {
		this.el.remove();
	}
}
`;

const DUSK_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="896" height="672" viewBox="0 0 896 672"><defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1b2a4a"/><stop offset="0.7" stop-color="#4d6b9a"/><stop offset="1" stop-color="#11151f"/></linearGradient></defs><rect width="896" height="672" fill="url(#sky)"/><circle cx="448" cy="380" r="120" fill="#c9d3e3"/><rect x="300" y="380" width="296" height="200" fill="#8f9bb0"/></svg>`;
const DUSK_URL = `data:image/svg+xml;base64,${Buffer.from(DUSK_SVG).toString('base64')}`;

async function mockFixturePlugin(page: Page) {
	await page.route('**/api/plugins/frontend-extensions', async (route: Route) => {
		const response = await route.fetch();
		const body = await response.json();
		body.data = body.data ?? {};
		body.data.tool_tabs = [
			{
				plugin_id: PLUGIN_ID,
				id: 'stamp',
				tool: `${PLUGIN_ID}.stamp`,
				label: 'Stamp tool',
				icon: 'brush',
				accepts: ['image'],
				handles: [],
				component: 'StampToolTab.js'
			}
		];
		body.data.revisions = { ...(body.data.revisions ?? {}), [PLUGIN_ID]: 'e2e' };
		await route.fulfill({ response, json: body });
	});
	await page.route(`**/api/plugins/${PLUGIN_ID}/assets/StampToolTab.js*`, (route) =>
		route.fulfill({ status: 200, contentType: 'text/javascript', body: STAMP_MODULE })
	);
}

function bookTab(page: Page, name: string) {
	return page.locator('button.book-tab', { hasText: name });
}

async function openGenerate(page: Page) {
	await loginAsOwner(page);
	await page.goto('/generate');
	await expect(page.locator('.tab-bar-container')).toBeVisible({ timeout: 15000 });
	await expect(bookTab(page, 'Generation 1')).toBeVisible();
}

test.describe('tool tabs on desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('an Image editor tab takes the whole tab area, renames, survives a reload and asks before discarding edits', async ({ page }) => {
		await openGenerate(page);
		await expect(page.locator('.generation-panel')).toBeVisible();

		await page.getByRole('button', { name: 'Add new tab' }).click();
		const menu = page.getByRole('menu', { name: 'New tab' });
		await expect(menu).toBeVisible();
		await expect(menu.getByRole('menuitem')).toHaveText([/New generation/, /Image editor/, /Video editor/]);
		await expect(menu.getByTestId('no-plugin-tools')).toContainText('No plugin tools yet');
		await screenshot(page, JOURNEY, 'plus-menu-1440');

		await menu.getByRole('menuitem', { name: /Image editor/ }).click();
		const host = page.getByTestId('tool-tab-host');
		await expect(host).toHaveAttribute('data-tool', 'image');
		await expect(host.getByTestId('tool-tab-empty')).toContainText('Open a picture to edit');
		await expect(page.locator('.generation-panel')).toHaveCount(0);
		await expect(page.getByTestId('context-bar')).toHaveCount(0);
		await expect(page.locator('.workbench-pane, [data-testid="workbench"]')).toHaveCount(0);
		await expect(bookTab(page, 'Image editor').getByTestId('tab-tool-thumb')).toBeVisible();
		await screenshot(page, JOURNEY, 'image-editor-empty-1440');

		const generationRequests: string[] = [];
		page.on('request', (request) => {
			if (request.method() === 'POST' && /\/api\/generations?(\/|$|\?)/.test(new URL(request.url()).pathname)) generationRequests.push(request.url());
		});
		const panelFlags = async () => {
			const k = await scopedStorageKey(page, 'potionui_tabs_state');
			return page.evaluate((storageKey) => {
				const saved = JSON.parse(localStorage.getItem(storageKey) ?? '{"tabs":[]}');
				const generation = saved.tabs.find((tab: { kind?: string }) => tab.kind !== 'tool') ?? {};
				return { left: !!generation.leftPanelCollapsed, workbench: !!generation.workbenchCollapsed };
			}, k);
		};
		const before = await panelFlags();
		await host.click({ position: { x: 40, y: 300 } });
		for (const key of ['g', 'f', 'e']) await page.keyboard.press(key);
		await page.waitForTimeout(800);
		expect(generationRequests).toEqual([]);
		expect(await panelFlags()).toEqual(before);
		await expect(host).toHaveAttribute('data-tool', 'image');
		await expect(page.locator('.generation-panel')).toHaveCount(0);

		await bookTab(page, 'Image editor').dblclick();
		const input = page.locator('.book-tab input.tab-input');
		await input.fill('Hero shot');
		await input.press('Enter');
		await expect(bookTab(page, 'Hero shot')).toBeVisible();

		const key = await scopedStorageKey(page, 'potionui_tabs_state');
		await expect
			.poll(async () => page.evaluate((k) => localStorage.getItem(k) ?? '', key))
			.toContain('"name":"Hero shot"');
		await page.evaluate(
			({ k, url }) => {
				const saved = JSON.parse(localStorage.getItem(k)!);
				for (const tab of saved.tabs) {
					if (tab.kind !== 'tool') continue;
					tab.dirty = 2;
					tab.source = { path: '/media/observatory-dusk.svg', url, name: 'observatory-dusk.svg' };
				}
				localStorage.setItem(k, JSON.stringify(saved));
			},
			{ k: key, url: DUSK_URL }
		);

		await page.reload();
		await expect(bookTab(page, 'Hero shot')).toBeVisible({ timeout: 15000 });
		await expect(page.getByTestId('tool-tab-host')).toHaveAttribute('data-tool', 'image');
		await expect(page.locator('[data-editor-layer]')).toBeVisible({ timeout: 15000 });
		await expect.poll(() => page.locator('[data-editor-layer]').evaluate((canvas) => (canvas as HTMLCanvasElement).width)).toBeGreaterThan(1);
		await expect(bookTab(page, 'Hero shot').locator('[data-testid="tab-tool-thumb"] img')).toHaveAttribute('src', DUSK_URL);
		await screenshot(page, JOURNEY, 'image-editor-source-1440');
		await expect(bookTab(page, 'Hero shot').getByTestId('tab-unsaved')).toBeVisible();
		await expect(page.locator('.generation-panel')).toHaveCount(0);

		await bookTab(page, 'Hero shot').locator('.close-button').click({ force: true });
		const dialog = page.getByRole('alertdialog');
		await expect(dialog).toContainText('Discard 2 unsaved edits?');
		await screenshot(page, JOURNEY, 'discard-prompt-1440');
		await dialog.getByRole('button', { name: 'Discard and close' }).click();
		await expect(bookTab(page, 'Hero shot')).toHaveCount(0);

		await expect(page.locator('.generation-panel')).toBeVisible();
		await expect(page.getByTestId('tool-tab-host')).toHaveCount(0);
	});

	test('a generation tab still works next to a tool tab, and T opens one directly', async ({ page }) => {
		await openGenerate(page);
		await page.getByRole('button', { name: 'Add new tab' }).click();
		await page.getByRole('menuitem', { name: /Video editor/ }).click();
		await expect(page.getByTestId('tool-tab-host')).toHaveAttribute('data-tool', 'video');
		await expect(page.getByTestId('tool-tab-empty')).toContainText('Open a clip to edit');

		await bookTab(page, 'Generation 1').click();
		await expect(page.getByTestId('tool-tab-host')).toHaveCount(0);
		await expect(page.locator('.generation-panel')).toBeVisible();
		await expect(page.locator('.generation-panel .generate-button')).toBeVisible();

		await bookTab(page, 'Video editor').click();
		await page.getByTestId('tool-tab-host').click({ position: { x: 20, y: 200 } });
		await page.keyboard.press('t');
		await expect(bookTab(page, 'Generation 2')).toBeVisible();
		await expect(page.locator('.generation-panel')).toBeVisible();
		await expect(page.getByTestId('tool-tab-host')).toHaveCount(0);
	});

	test('a plugin tool tab is listed under From plugins and mounts the plugin component', async ({ page }) => {
		await mockFixturePlugin(page);
		await openGenerate(page);

		await page.getByRole('button', { name: 'Add new tab' }).click();
		const menu = page.getByRole('menu', { name: 'New tab' });
		await expect(menu.getByTestId('no-plugin-tools')).toHaveCount(0);
		await menu.getByRole('menuitem', { name: /Stamp tool/ }).click();

		const stamp = page.getByTestId('stamp-tool');
		await expect(stamp).toContainText('stamps 0');
		await stamp.getByRole('button', { name: 'Stamp twice' }).click();
		await expect(stamp).toContainText('stamps 2');
		await expect(bookTab(page, 'Stamp tool').getByTestId('tab-unsaved')).toBeVisible();

		await bookTab(page, 'Stamp tool').locator('.close-button').click({ force: true });
		await expect(page.getByRole('alertdialog')).toContainText('Discard 2 unsaved edits?');
		await page.getByRole('alertdialog').getByRole('button', { name: 'Cancel' }).click();
		await expect(bookTab(page, 'Stamp tool')).toBeVisible();
	});
});

test.describe('tool tabs on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

	test('the Tabs sheet lists every tab and opens an editor with its own top bar', async ({ page }) => {
		await loginAsOwner(page);
		await page.goto('/generate');
		await expect(page.locator('.studio-topbar')).toBeVisible({ timeout: 15000 });

		await page.getByRole('button', { name: /^Open tabs/ }).click();
		const sheet = page.getByRole('dialog', { name: 'Tabs' });
		await expect(sheet).toBeVisible();
		await expect(sheet.getByTestId('tabs-sheet-row')).toHaveCount(1);
		await expect(sheet.getByTestId('tabs-sheet-new').getByRole('button')).toHaveText(['Generation', 'Image editor', 'Video editor']);
		await expect(sheet).toContainText('Tools that plugins add show up here too.');
		await screenshot(page, JOURNEY, 'tabs-sheet-generation-390');

		await sheet.getByRole('button', { name: 'Image editor' }).click();
		const phone = page.getByTestId('phone-tool-tab');
		await expect(phone).toBeVisible();
		await expect(phone.getByRole('button', { name: /^Open tabs/ })).toContainText('Image editor');
		await expect(phone.getByTestId('tool-tab-empty')).toContainText('Open a picture to edit');
		await screenshot(page, JOURNEY, 'phone-image-editor-390');
		await expect(phone.getByRole('button', { name: 'Undo' })).toBeDisabled();
		await expect(phone.getByRole('button', { name: 'Save' })).toBeDisabled();
		await expect(page.locator('.studio-dock')).toHaveCount(0);
		await expect(page.getByRole('button', { name: 'Open preset and session' })).toHaveCount(0);
		await expect(page.locator('nav.fixed.bottom-0').filter({ hasText: 'Generate' })).toBeVisible();

		const overflowX = await page.evaluate(() => {
			const el = document.scrollingElement!;
			return el.scrollWidth - el.clientWidth;
		});
		expect(overflowX).toBeLessThanOrEqual(0);

		await phone.getByRole('button', { name: /^Open tabs/ }).click();
		const again = page.getByRole('dialog', { name: 'Tabs' });
		await expect(again.getByTestId('tabs-sheet-row')).toHaveCount(2);
		await expect(again.getByTestId('tabs-sheet-row').nth(1)).toContainText('Image editor · empty');
		await screenshot(page, JOURNEY, 'tabs-sheet-tool-390');

		await again.getByTestId('tabs-sheet-row').first().getByRole('button').first().click();
		await expect(page.locator('.studio-dock')).toBeVisible();
		await expect(page.getByTestId('phone-tool-tab')).toHaveCount(0);
	});
});
