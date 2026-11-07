// @vitest-environment jsdom
import { describe, it, expect, vi, beforeAll, afterAll, afterEach } from 'vitest';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { mount, unmount } from 'svelte';
import { page } from './stubs/appStores';
import { settle } from './helpers/settle';

const SCOPE = '[data-potionui-plugin="styled-plugin"]';

const state = vi.hoisted(() => ({ base: '', pageInfo: {} as Record<string, unknown> }));

vi.mock('$lib/services/api/index', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api/index')>('$lib/services/api/index');
	return {
		...actual,
		api: {
			...actual.api,
			getBaseURL: () => state.base,
			getToken: () => 'token',
			triggerAuthExpired: () => {},
			getClient: () => ({
				request: async () => ({
					status: 200,
					statusText: 'OK',
					data: JSON.stringify({ success: true, data: state.pageInfo }),
					headers: {},
					config: {}
				})
			})
		}
	};
});

let stage: string;

beforeAll(() => {
	const underTest = resolve(__dirname, '../../node_modules/.plugin-page-under-test');
	mkdirSync(underTest, { recursive: true });
	stage = mkdtempSync(join(underTest, 'run-'));
	const assets = join(stage, 'api', 'plugins', 'styled-plugin', 'assets');
	mkdirSync(assets, { recursive: true });
	writeFileSync(
		join(assets, 'StyledPage.js'),
		"export function mountPlugin(el) { el.innerHTML = '<p class=\"inner\">page</p>'; return {}; }\nexport function unmountPlugin() {}\n"
	);
	state.base = pathToFileURL(stage).href;
	page.update((current) => ({ ...current, params: { pluginId: 'styled' } }));
});

afterAll(() => {
	rmSync(stage, { recursive: true, force: true });
});

afterEach(() => {
	vi.unstubAllGlobals();
	document.head.querySelectorAll('style').forEach((style) => style.remove());
	document.body.innerHTML = '';
});

async function mountPage() {
	const { default: PluginPage } = await import('../../src/routes/plugins/[pluginId]/[...path]/+page.svelte');
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = mount(PluginPage, { target });
	await settle(20);
	return component;
}

function pageStyles(): string {
	return Array.from(document.head.querySelectorAll('style'), (style) => style.textContent ?? '').join('\n');
}

describe('the plugin page route', () => {
	it('mounts the page in an element carrying the plugin id', async () => {
		state.pageInfo = { plugin_id: 'styled-plugin', route: 'styled', component_path: 'StyledPage.js', label: 'Styled', stylesheet: null };

		const component = await mountPage();

		const container = document.querySelector('.plugin-container');
		expect(container?.getAttribute('data-potionui-plugin')).toBe('styled-plugin');
		expect(container?.querySelector('.inner')?.textContent).toBe('page');
		unmount(component);
	});

	it('applies the stylesheet of the page while it is mounted, without its rules that reach outside', async () => {
		const fetchImpl = vi.fn(async () => new Response(`${SCOPE} .inner { color: red } .host-leak { color: blue }`));
		vi.stubGlobal('fetch', fetchImpl);
		state.pageInfo = { plugin_id: 'styled-plugin', route: 'styled', component_path: 'StyledPage.js', label: 'Styled', stylesheet: 'StyledPage.css' };

		const component = await mountPage();

		expect(fetchImpl).toHaveBeenCalledWith(`${state.base}/api/plugins/styled-plugin/assets/StyledPage.css`);
		expect(pageStyles()).toContain(`${SCOPE} .inner`);
		expect(pageStyles()).not.toContain('.host-leak');

		unmount(component);
		expect(pageStyles()).not.toContain(`${SCOPE} .inner`);
	});

	it('loads no stylesheet for a page that has none', async () => {
		const fetchImpl = vi.fn();
		vi.stubGlobal('fetch', fetchImpl);
		state.pageInfo = { plugin_id: 'styled-plugin', route: 'styled', component_path: 'StyledPage.js', label: 'Styled', stylesheet: null };

		const component = await mountPage();

		expect(fetchImpl).not.toHaveBeenCalled();
		expect(document.querySelector('.inner')).not.toBeNull();
		expect(pageStyles()).toBe('');
		unmount(component);
	});

	it('mounts the page even when its stylesheet does not load', async () => {
		vi.stubGlobal('fetch', vi.fn(async () => new Response('missing', { status: 404 })));
		state.pageInfo = { plugin_id: 'styled-plugin', route: 'styled', component_path: 'StyledPage.js', label: 'Styled', stylesheet: 'StyledPage.css' };

		const component = await mountPage();

		expect(document.querySelector('.inner')?.textContent).toBe('page');
		expect(pageStyles()).toBe('');
		unmount(component);
	});
});
