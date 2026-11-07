// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import {
	attachPluginStylesheet,
	loadPluginStylesheet,
	pluginScopeSelector,
	scopedPluginStylesheet,
	splitSelectorList
} from './pluginStylesheet';

const SCOPE = '[data-potionui-plugin="video-producer"]';
const BASE = 'http://localhost/api/plugins/video-producer/assets/VideoProducerPage.css';

function kept(css: string): string[] {
	const { sheet } = scopedPluginStylesheet('video-producer', css, BASE);
	return Array.from(sheet.cssRules, (rule) => rule.cssText.replace(/\s+/g, ' ').trim());
}

describe('scopedPluginStylesheet', () => {
	it('keeps the rules under the page of the plugin', () => {
		const css = `${SCOPE} .p-4 { padding: 1rem } ${SCOPE} > .grid { display: grid } ${SCOPE} { --gap: 4px }`;

		const { dropped } = scopedPluginStylesheet('video-producer', css, BASE);

		expect(kept(css)).toHaveLength(3);
		expect(dropped).toBe(0);
	});

	it('drops the rules that reach the rest of the app', () => {
		const css = `.p-4 { padding: 1rem } body { margin: 0 } :root { --canvas: 0 0 0 } * { box-sizing: content-box } ${SCOPE} .kept { color: red }`;

		const { dropped } = scopedPluginStylesheet('video-producer', css, BASE);

		expect(kept(css)).toEqual([`${SCOPE} .kept { color: red; }`]);
		expect(dropped).toBe(4);
	});

	it('drops a rule whose selector reaches a sibling of the page', () => {
		const css = `${SCOPE} ~ aside { display: none } ${SCOPE} + * { display: none } ${SCOPE}.wide .x { color: red }`;

		expect(kept(css)).toEqual([]);
	});

	it('drops a rule whose selector list has one selector outside the page', () => {
		const css = `${SCOPE} .a, .b { color: red }`;

		expect(kept(css)).toEqual([]);
	});

	it('drops the rules of another plugin', () => {
		const css = `[data-potionui-plugin="other"] .a { color: red }`;

		expect(kept(css)).toEqual([]);
	});

	it('reads the scope written without quotes or with single quotes', () => {
		const css = `[data-potionui-plugin=video-producer] .a { color: red } [data-potionui-plugin='video-producer'] .b { color: blue }`;

		expect(kept(css)).toHaveLength(2);
	});

	it('looks inside media and supports rules and drops what is outside the page there', () => {
		const css = `@media (min-width: 768px) { ${SCOPE} .md\\:flex { display: flex } .hidden { display: none } } @supports (display: grid) { .x { color: red } }`;

		const rules = kept(css);

		expect(rules).toHaveLength(2);
		expect(rules[0]).toContain('.md\\:flex');
		expect(rules[0]).not.toContain('.hidden');
		expect(rules[1]).not.toContain('.x');
	});

	it('keeps keyframes and font faces', () => {
		const css = `@keyframes spin { to { transform: rotate(360deg) } } @font-face { font-family: Plugin; src: url(plugin.woff2) }`;

		expect(kept(css)).toHaveLength(2);
	});

	it('drops imports and page rules', () => {
		const css = `@import url(https://example.com/x.css); @page { margin: 0 } ${SCOPE} .a { color: red }`;

		const { dropped } = scopedPluginStylesheet('video-producer', css, BASE);

		expect(kept(css)).toEqual([`${SCOPE} .a { color: red; }`]);
		expect(dropped).toBe(2);
	});
});

describe('splitSelectorList', () => {
	it('splits on the commas of the list only', () => {
		expect(splitSelectorList(`a:is(.b, .c), [title="d, e"] f, g`)).toEqual([`a:is(.b, .c)`, `[title="d, e"] f`, 'g']);
	});

	it('keeps an escaped comma inside its class name', () => {
		expect(splitSelectorList(`${SCOPE} .grid-cols-\\[minmax\\(0\\,1fr\\)\\], .b`)).toEqual([
			`${SCOPE} .grid-cols-\\[minmax\\(0\\,1fr\\)\\]`,
			'.b'
		]);
		expect(splitSelectorList(`${SCOPE} .a\\,b, .c`)).toEqual([`${SCOPE} .a\\,b`, '.c']);
	});
});

describe('pluginScopeSelector', () => {
	it('quotes the plugin id', () => {
		expect(pluginScopeSelector('video-producer')).toBe(SCOPE);
		expect(pluginScopeSelector('a"b')).toBe('[data-potionui-plugin="a\\"b"]');
	});
});

describe('attachPluginStylesheet', () => {
	afterEach(() => {
		delete (document as { adoptedStyleSheets?: unknown }).adoptedStyleSheets;
		document.head.innerHTML = '';
	});

	it('adopts the sheet and gives it back on detach', () => {
		const other = new CSSStyleSheet();
		Object.defineProperty(document, 'adoptedStyleSheets', { value: [other], writable: true, configurable: true });
		const { sheet } = scopedPluginStylesheet('video-producer', `${SCOPE} .a { color: red }`, BASE);

		const detach = attachPluginStylesheet(document, sheet);
		expect(document.adoptedStyleSheets).toEqual([other, sheet]);

		detach();
		expect(document.adoptedStyleSheets).toEqual([other]);
	});

	it('falls back to a style element holding only the kept rules', () => {
		const { sheet } = scopedPluginStylesheet('video-producer', `.gone { color: red } ${SCOPE} .a { color: red }`, BASE);

		const detach = attachPluginStylesheet(document, sheet);
		const style = document.head.querySelector('style');
		expect(style?.textContent).toContain(`${SCOPE} .a`);
		expect(style?.textContent).not.toContain('.gone');

		detach();
		expect(document.head.querySelector('style')).toBeNull();
	});
});

describe('loadPluginStylesheet', () => {
	it('gives the scoped sheet of a stylesheet that loads', async () => {
		const fetchImpl = vi.fn(async () => new Response(`${SCOPE} .a { color: red } .b { color: blue }`, { status: 200 }));

		const sheet = await loadPluginStylesheet('video-producer', BASE, fetchImpl as unknown as typeof fetch);

		expect(fetchImpl).toHaveBeenCalledWith(BASE);
		expect(sheet?.cssRules).toHaveLength(1);
	});

	it('gives nothing when the stylesheet answers an error', async () => {
		const fetchImpl = vi.fn(async () => new Response('missing', { status: 404 }));

		expect(await loadPluginStylesheet('video-producer', BASE, fetchImpl as unknown as typeof fetch)).toBeNull();
	});

	it('gives nothing when the request fails', async () => {
		const fetchImpl = vi.fn(async () => {
			throw new TypeError('offline');
		});

		expect(await loadPluginStylesheet('video-producer', BASE, fetchImpl as unknown as typeof fetch)).toBeNull();
	});
});
