import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/plugin-api/componentResolver', () => ({
	resolvePluginComponent: vi.fn()
}));

import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';
import { chatToolRendererRegistry } from './chatToolRendererRegistry';

describe('chatToolRendererRegistry', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('resolves a statically registered core component', async () => {
		const Component = { name: 'UpdateFormSettingsPreview' };
		chatToolRendererRegistry.register('update_form_settings', { component: Component });

		expect(chatToolRendererRegistry.has('update_form_settings')).toBe(true);
		await expect(chatToolRendererRegistry.resolve('update_form_settings')).resolves.toBe(
			Component
		);
		expect(resolvePluginComponent).not.toHaveBeenCalled();
	});

	it('returns null for an unregistered tool name', async () => {
		await expect(chatToolRendererRegistry.resolve('totally_unknown_tool')).resolves.toBeNull();
	});

	it('resolves a lazy plugin entry via componentResolver and caches the result', async () => {
		const PluginComponent = { name: 'DatasetToolRenderer' };
		vi.mocked(resolvePluginComponent).mockResolvedValue(PluginComponent);

		chatToolRendererRegistry.register('dataset_tool', {
			pluginId: 'dataset-generator',
			asset: 'DatasetToolRenderer.js'
		});

		const first = await chatToolRendererRegistry.resolve('dataset_tool');
		const second = await chatToolRendererRegistry.resolve('dataset_tool');

		expect(first).toBe(PluginComponent);
		expect(second).toBe(PluginComponent);
		expect(resolvePluginComponent).toHaveBeenCalledTimes(1);
		expect(resolvePluginComponent).toHaveBeenCalledWith(
			'dataset-generator',
			'DatasetToolRenderer.js'
		);
	});

	it('unregister removes the entry and clears the cache', async () => {
		chatToolRendererRegistry.register('scratch_tool', { component: {} });
		expect(chatToolRendererRegistry.has('scratch_tool')).toBe(true);

		chatToolRendererRegistry.unregister('scratch_tool');

		expect(chatToolRendererRegistry.has('scratch_tool')).toBe(false);
		await expect(chatToolRendererRegistry.resolve('scratch_tool')).resolves.toBeNull();
	});

	it('keys reflects registered tool names', () => {
		chatToolRendererRegistry.register('some_tool', { component: {} });
		expect(chatToolRendererRegistry.keys()).toContain('some_tool');
	});
});
