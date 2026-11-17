// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from './helpers/mount';
import { settle } from './helpers/settle';
import { toolDto } from './fixtures/mediaToolCatalog';

vi.mock('$lib/services/api/index', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	api: new Proxy({}, { get: () => () => undefined })
}));

const { default: CatalogToolOptions } = await import('$lib/components/tool-tabs/image/CatalogToolOptions.svelte');

const TOOL = toolDto({
	id: 'color_key',
	label: 'Color key',
	params: [
		{ name: 'key_mode', type: 'select', options: ['auto', 'color'], default: 'auto' },
		{ name: 'key_color', type: 'color', default: '#00b140', when: { field: 'key_mode', equals: 'color' } },
		{ name: 'tolerance', type: 'float', label: 'Tolerance', min: 0, max: 100, default: 30 }
	]
} as never);

afterEach(() => {
	document.body.innerHTML = '';
});

describe('CatalogToolOptions', () => {
	it('draws the tool params as a form and reports the whole values once per change, never the commit', async () => {
		const changes: Array<Record<string, unknown>> = [];
		mountComponent(CatalogToolOptions as never, { tool: TOOL, values: { key_mode: 'auto', tolerance: 30 }, onchange: (next: Record<string, unknown>) => changes.push(next) });
		await settle(10);
		expect(document.body.querySelector('[data-catalog-tool="color_key"]')).not.toBeNull();
		expect(document.body.querySelector('[data-field-name="key_color"]')).toBeNull();
		const range = document.body.querySelector<HTMLInputElement>('[data-field-name="tolerance"] input[type="range"]')!;
		range.value = '55';
		range.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		range.dispatchEvent(new Event('change', { bubbles: true }));
		await settle(10);
		expect(changes).toEqual([{ key_mode: 'auto', tolerance: 55 }]);
	});

	it('says so when the tool has no settings', async () => {
		mountComponent(CatalogToolOptions as never, { tool: toolDto({ id: 'plain', label: 'Plain', params: [] } as never), values: {}, onchange: () => undefined });
		expect(document.body.textContent).toContain('Plain has no settings');
	});
});
