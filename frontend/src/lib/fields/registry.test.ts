import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/plugin-api/componentResolver', () => ({
	resolvePluginComponent: vi.fn()
}));

import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';
import {
	registerFieldComponent,
	unregisterFieldComponent,
	resolveFieldComponent,
	hasFieldComponent,
	listFieldTypes
} from './registry';

describe('fields/registry', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('resolves a statically registered component synchronously (wrapped in a promise)', async () => {
		const Component = { name: 'FakeComponent' };
		registerFieldComponent('string', { component: Component });

		expect(hasFieldComponent('string')).toBe(true);
		await expect(resolveFieldComponent('string')).resolves.toBe(Component);
		expect(resolvePluginComponent).not.toHaveBeenCalled();
	});

	it('resolves alias registrations to the same component', async () => {
		const Component = { name: 'NumberComponent' };
		registerFieldComponent('number', { component: Component });
		registerFieldComponent('integer', { component: Component });

		await expect(resolveFieldComponent('number')).resolves.toBe(Component);
		await expect(resolveFieldComponent('integer')).resolves.toBe(Component);
	});

	it('returns null for an unknown type', async () => {
		await expect(resolveFieldComponent('totally_unknown_type')).resolves.toBeNull();
	});

	it('resolves a lazy plugin entry via componentResolver and caches the result', async () => {
		const PluginComponent = { name: 'PluginComponent' };
		vi.mocked(resolvePluginComponent).mockResolvedValue(PluginComponent);

		registerFieldComponent('example_stars', { pluginId: 'example-field', asset: 'ExampleStarsField.js' });

		const first = await resolveFieldComponent('example_stars');
		const second = await resolveFieldComponent('example_stars');

		expect(first).toBe(PluginComponent);
		expect(second).toBe(PluginComponent);
		expect(resolvePluginComponent).toHaveBeenCalledTimes(1);
		expect(resolvePluginComponent).toHaveBeenCalledWith('example-field', 'ExampleStarsField.js');
	});

	it('unregisterFieldComponent removes the entry and clears the cache', async () => {
		registerFieldComponent('carousel', { component: {} });
		expect(hasFieldComponent('carousel')).toBe(true);

		unregisterFieldComponent('carousel');

		expect(hasFieldComponent('carousel')).toBe(false);
		await expect(resolveFieldComponent('carousel')).resolves.toBeNull();
	});

	it('listFieldTypes reflects registered types', () => {
		registerFieldComponent('select', { component: {} });
		expect(listFieldTypes()).toContain('select');
	});
});
