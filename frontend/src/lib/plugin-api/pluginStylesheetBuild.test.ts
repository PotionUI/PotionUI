import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { createRequire } from 'node:module';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { transform } from 'esbuild';
import { PLUGIN_SCOPE_ATTRIBUTE, pluginScopeSelector, scopedPluginStylesheet } from './pluginStylesheet';

const REPO = resolve(__dirname, '../../../..');
const FRONTEND = join(REPO, 'frontend');
const SCOPE = ':where([data-potionui-plugin="styled-plugin"])';
const frontendRequire = createRequire(join(FRONTEND, 'package.json'));

let build: any;
let work: string;
let css: string;

function selectorsOf(text: string): string[] {
	const sheet = new CSSStyleSheet();
	sheet.replaceSync(text);
	const selectors: string[] = [];
	const walk = (rules: CSSRuleList) => {
		for (const rule of Array.from(rules)) {
			if ('selectorText' in rule) selectors.push((rule as CSSStyleRule).selectorText);
			else if ('cssRules' in rule && rule.constructor.name !== 'CSSKeyframesRule') walk((rule as CSSGroupingRule).cssRules);
		}
	};
	walk(sheet.cssRules);
	return selectors;
}

beforeAll(async () => {
	(globalThis as { CSSStyleSheet?: unknown }).CSSStyleSheet = new (frontendRequire('jsdom').JSDOM)('').window.CSSStyleSheet;
	build = await import(pathToFileURL(join(REPO, 'scripts', 'plugin-stylesheet.mjs')).href);
	work = mkdtempSync(join(tmpdir(), 'plugin-stylesheet-'));
	const src = join(work, 'styled-plugin', 'frontend', 'src');
	mkdirSync(src, { recursive: true });
	writeFileSync(
		join(src, 'StyledPage.svelte'),
		'<div class="hidden md:flex bg-surface-2 text-fg-muted grid-cols-[260px_1fr] grid-cols-[minmax(0,1fr)_auto] animate-spin before:content-[\'\'] focus-visible:ring-2"></div>\n'
	);
	writeFileSync(join(src, 'StyledPage.css'), '@tailwind utilities;\n:root { --plugin-gap: 4px; }\nbody .note { color: red; }\n.note { margin: 0; }\n');
	css = await build.compilePluginStylesheet({
		pluginId: 'styled-plugin',
		pluginDir: join(work, 'styled-plugin'),
		source: join(src, 'StyledPage.css'),
		frontendDir: FRONTEND
	});
});

afterAll(() => {
	delete (globalThis as { CSSStyleSheet?: unknown }).CSSStyleSheet;
	rmSync(work, { recursive: true, force: true });
});

describe('compilePluginStylesheet', () => {
	it('puts every rule under the page of the plugin', () => {
		const selectors = selectorsOf(css);

		expect(selectors.length).toBeGreaterThan(5);
		for (const selector of selectors) expect(selector.startsWith(SCOPE)).toBe(true);
	});

	it('compiles the classes of the plugin sources with the tokens and variants of the app', () => {
		expect(css).toContain('rgb(var(--surface-2)');
		expect(css).toContain('rgb(var(--fg-muted)');
		expect(css).toMatch(/grid-cols-\\\[260px_1fr\\\]/);
		expect(css).toContain('@media (min-width: 768px)');
		expect(css).toMatch(/@keyframes spin \{\s*to \{/);
		expect(css).toContain(":where(html:not([data-input='pointer']) *)");
	});

	it('leaves out the base styles and the classes the plugin does not use', () => {
		expect(css).not.toMatch(/(^|[\s,}])(button|img|h1)[\s,{]/);
		expect(css).not.toContain('.p-8');
	});

	it('turns the document root into the page and puts the rules of the plugin under it', () => {
		expect(css).toContain(`${SCOPE} { --plugin-gap: 4px; }`);
		expect(css).toContain(`${SCOPE} .note { color: red; }`);
		expect(css).toContain(`${SCOPE} .note { margin: 0; }`);
		expect(css).not.toContain(':root');
		expect(css).not.toContain('body');
	});

	it('gives a minified sheet that the page keeps whole', async () => {
		const { code } = await transform(css, { loader: 'css', minify: true, legalComments: 'eof' });

		const { sheet, dropped } = scopedPluginStylesheet('styled-plugin', code, 'http://localhost/');

		expect(dropped).toBe(0);
		expect(sheet.cssRules.length).toBeGreaterThan(5);
	});
});

describe('the scope of a plugin page', () => {
	it('is the same attribute in the build, the page loader and the plugin page route', () => {
		const route = readFileSync(join(FRONTEND, 'src', 'routes', 'plugins', '[pluginId]', '[...path]', '+page.svelte'), 'utf-8');

		expect(build.PLUGIN_SCOPE_ATTRIBUTE).toBe(PLUGIN_SCOPE_ATTRIBUTE);
		expect(build.pluginScopeSelector('a"b')).toBe(pluginScopeSelector('a"b'));
		expect(route).toContain(`${PLUGIN_SCOPE_ATTRIBUTE}={pageInfo?.plugin_id}`);
	});
});
