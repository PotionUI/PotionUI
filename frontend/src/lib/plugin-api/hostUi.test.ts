import { describe, it, expect, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { getRegistry } from './componentRegistry';
import { registerHostUiComponents } from './hostUi';

function uiIndexExports(): string[] {
	const source = readFileSync(join(__dirname, '../components/ui/index.ts'), 'utf-8');
	return [...source.matchAll(/export \{ default as (\w+) \}/g)].map((m) => m[1]);
}

const existing = { mount: () => null, update: () => {}, unmount: () => {} };

describe('host ui component registration', () => {
	const original = { ...getRegistry() };

	afterEach(() => {
		const registry = getRegistry();
		for (const name of Object.keys(registry)) {
			if (!(name in original)) delete registry[name];
		}
		Object.assign(registry, original);
	});

	it('registers every component exported from the ui index', () => {
		registerHostUiComponents();
		const registered = Object.keys(getRegistry());
		const expected = uiIndexExports();
		expect(expected.length).toBeGreaterThan(10);
		expect(expected.filter((name) => !registered.includes(name))).toEqual([]);
	});

	it('registers the shared pieces that live outside ui', () => {
		registerHostUiComponents();
		const registered = Object.keys(getRegistry());
		for (const name of [
			'Tooltip',
			'Icon',
			'DetailLayout',
			'DetailHeader',
			'DetailBody',
			'LibraryFilterBar',
			'LibraryPage',
			'LibraryShell',
			'DynamicForm',
			'GenerationDetailsModal',
			'MediaPreviewModal'
		]) {
			expect(registered, name).toContain(name);
		}
	});

	it('keeps an already registered component instead of replacing it', () => {
		const registry = getRegistry();
		registry.Button = existing;
		registerHostUiComponents();
		expect(registry.Button).toBe(existing);
	});

	it('does not carry a replaced entry over from the previous test', () => {
		registerHostUiComponents();
		expect(getRegistry().Button).not.toBe(existing);
	});
});
