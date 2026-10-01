import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { getRegistry } from './componentRegistry';
import { registerHostUiComponents } from './hostUi';

function uiIndexExports(): string[] {
	const source = readFileSync(join(__dirname, '../components/ui/index.ts'), 'utf-8');
	return [...source.matchAll(/export \{ default as (\w+) \}/g)].map((m) => m[1]);
}

describe('host ui component registration', () => {
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
			'LibraryShell'
		]) {
			expect(registered, name).toContain(name);
		}
	});

	it('keeps an already registered component instead of replacing it', () => {
		const registry = getRegistry();
		const existing = { mount: () => null, update: () => {}, unmount: () => {} };
		registry.Button = existing;
		registerHostUiComponents();
		expect(registry.Button).toBe(existing);
	});
});
