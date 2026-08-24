import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/plugin-api/componentResolver', () => ({
	resolvePluginComponent: vi.fn()
}));

import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';
import { artifactRendererRegistry } from './artifactRendererRegistry';

describe('artifactRendererRegistry', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('resolves a statically registered core component', async () => {
		const Component = { name: 'SeedArtifact' };
		artifactRendererRegistry.register('seed', { component: Component });

		expect(artifactRendererRegistry.has('seed')).toBe(true);
		await expect(artifactRendererRegistry.resolve('seed')).resolves.toBe(Component);
		expect(resolvePluginComponent).not.toHaveBeenCalled();
	});

	it('returns null for an unregistered artifact_type', async () => {
		await expect(artifactRendererRegistry.resolve('totally_unknown_artifact')).resolves.toBeNull();
	});

	it('resolves a lazy plugin entry via componentResolver and caches the result', async () => {
		const PluginComponent = { name: 'FakeArtifact' };
		vi.mocked(resolvePluginComponent).mockResolvedValue(PluginComponent);

		artifactRendererRegistry.register('fake_artifact', { pluginId: 'example-extensions', asset: 'FakeArtifact.js' });

		const first = await artifactRendererRegistry.resolve('fake_artifact');
		const second = await artifactRendererRegistry.resolve('fake_artifact');

		expect(first).toBe(PluginComponent);
		expect(second).toBe(PluginComponent);
		expect(resolvePluginComponent).toHaveBeenCalledTimes(1);
		expect(resolvePluginComponent).toHaveBeenCalledWith('example-extensions', 'FakeArtifact.js');
	});

	it('unregister removes the entry and clears the cache', async () => {
		artifactRendererRegistry.register('scratch', { component: {} });
		expect(artifactRendererRegistry.has('scratch')).toBe(true);

		artifactRendererRegistry.unregister('scratch');

		expect(artifactRendererRegistry.has('scratch')).toBe(false);
		await expect(artifactRendererRegistry.resolve('scratch')).resolves.toBeNull();
	});

	it('keys reflects registered artifact types', () => {
		artifactRendererRegistry.register('workflow', { component: {} });
		expect(artifactRendererRegistry.keys()).toContain('workflow');
	});
});
