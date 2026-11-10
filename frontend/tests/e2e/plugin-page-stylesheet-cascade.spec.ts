import { test, expect, type Page, type Locator } from '@playwright/test';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { transform } from 'esbuild';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'plugin-page-stylesheet-cascade';
const PLUGIN_ID = 'cascade-fixture';
const REPO = fileURLToPath(new URL('../../../', import.meta.url));

const PAGE_MODULE = `
export function mountPlugin(el, ctx) {
	el.innerHTML = '<div class="p-6 flex flex-col gap-4 items-start">'
		+ '<div data-testid="plugin-own" class="bg-surface-3 w-[123px] h-[17px]"></div>'
		+ '<span data-testid="host-button-slot"></span>'
		+ '</div>';
	const Button = ctx.hostComponents.Button;
	const instance = Button.mount(el.querySelector('[data-testid="host-button-slot"]'), { variant: 'secondary', children: 'Host button' });
	return { Button, instance };
}
export function unmountPlugin(handle) {
	handle.Button.unmount(handle.instance);
}
`;

let sheet: string;
let work: string;

test.beforeAll(async () => {
	work = mkdtempSync(join(tmpdir(), 'plugin-cascade-'));
	const src = join(work, PLUGIN_ID, 'frontend', 'src');
	mkdirSync(src, { recursive: true });
	writeFileSync(join(src, 'CascadePage.js'), PAGE_MODULE);
	writeFileSync(join(src, 'CascadePage.css'), '@tailwind utilities;\n');
	const build = await import(pathToFileURL(join(REPO, 'scripts', 'plugin-stylesheet.mjs')).href);
	const css = await build.compilePluginStylesheet({
		pluginId: PLUGIN_ID,
		pluginDir: join(work, PLUGIN_ID),
		source: join(src, 'CascadePage.css'),
		frontendDir: join(REPO, 'frontend')
	});
	sheet = (await transform(css, { loader: 'css', minify: true, legalComments: 'eof' })).code;
});

test.afterAll(() => {
	rmSync(work, { recursive: true, force: true });
});

async function servePluginPage(page: Page) {
	await page.route(`**/api/plugins/pages/${PLUGIN_ID}`, (route) =>
		route.fulfill({
			contentType: 'application/json',
			body: JSON.stringify({
				success: true,
				data: { plugin_id: PLUGIN_ID, route: PLUGIN_ID, component_path: 'CascadePage.js', label: 'Cascade', stylesheet: 'CascadePage.css' }
			})
		})
	);
	await page.route(`**/api/plugins/${PLUGIN_ID}/assets/CascadePage.js`, (route) =>
		route.fulfill({ contentType: 'text/javascript', body: PAGE_MODULE })
	);
	await page.route(`**/api/plugins/${PLUGIN_ID}/assets/CascadePage.css`, (route) => route.fulfill({ contentType: 'text/css', body: sheet }));
}

function background(locator: Locator): Promise<string> {
	return locator.evaluate((el) => getComputedStyle(el).backgroundColor);
}

async function tokenBackground(page: Page, token: string): Promise<string> {
	return page.evaluate((name) => {
		const probe = document.createElement('div');
		probe.style.backgroundColor = `rgb(var(--${name}))`;
		document.body.appendChild(probe);
		const color = getComputedStyle(probe).backgroundColor;
		probe.remove();
		return color;
	}, token);
}

test('a host button inside a plugin page with a stylesheet keeps the hover style of the app', async ({ page }) => {
	await page.setViewportSize({ width: 1280, height: 800 });
	await loginAsOwner(page);
	await servePluginPage(page);

	await page.goto(`/plugins/${PLUGIN_ID}`);

	const scope = page.locator(`[data-potionui-plugin="${PLUGIN_ID}"]`);
	const own = scope.getByTestId('plugin-own');
	const button = scope.getByRole('button', { name: 'Host button' });
	await expect(button).toBeVisible({ timeout: 20000 });
	await expect.poll(() => own.evaluate((el) => getComputedStyle(el).width)).toBe('123px');

	const rest = await tokenBackground(page, 'surface-3');
	const hovered = await tokenBackground(page, 'line-hover');
	expect(hovered).not.toBe(rest);
	expect(sheet).toContain('.bg-surface-3');
	expect(await background(own)).toBe(rest);
	await page.mouse.move(0, 0);
	await expect.poll(() => background(button)).toBe(rest);

	await button.hover();
	await expect.poll(() => background(button), { timeout: 2000 }).toBe(hovered);
	await screenshot(page, JOURNEY, 'host-button-hovered');
});
