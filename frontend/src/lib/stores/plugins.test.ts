import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPut = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: () => ({
			get: (...args: unknown[]) => mockGet(...args),
			post: (...args: unknown[]) => mockPost(...args),
			put: (...args: unknown[]) => mockPut(...args)
		})
	}
}));

// Module-level singleton store - each test gets a fresh instance.
async function freshStore() {
	vi.resetModules();
	return import('./plugins');
}

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (reason?: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

const PLUGIN = {
	id: 'p1',
	name: 'Test Plugin',
	version: '1.0.0',
	type: 'backend-only' as const,
	enabled: false,
	manifest_path: '/plugins/test'
};

describe('stores/plugins pendingPluginIds', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('togglePlugin tracks the id in pendingPluginIds, not the whole-catalogue loading flag', async () => {
		const { pluginStore, plugins, loading, pendingPluginIds } = await freshStore();
		plugins.set([{ ...PLUGIN }]);

		const gate = deferred<{ data: unknown }>();
		mockPost.mockReturnValueOnce(gate.promise);

		const togglePromise = pluginStore.togglePlugin('p1', true);

		// While the request is in flight: the toggling plugin is pending, but
		// `loading` - which gates the whole plugin list in PluginsTab - must
		// stay false. This used to be `loading`,
		// which blanked every row for the duration of one row's request.
		expect(get(pendingPluginIds).has('p1')).toBe(true);
		expect(get(loading)).toBe(false);

		gate.resolve({ data: { success: true } });
		await togglePromise;

		expect(get(pendingPluginIds).has('p1')).toBe(false);
		expect(get(loading)).toBe(false);
		expect(get(plugins)[0].enabled).toBe(true);
	});

	it('clears the pending id even when the request fails', async () => {
		const { pluginStore, plugins, pendingPluginIds } = await freshStore();
		plugins.set([{ ...PLUGIN }]);
		mockPost.mockRejectedValueOnce(new Error('network error'));

		const ok = await pluginStore.togglePlugin('p1', true);

		expect(ok).toBe(false);
		expect(get(pendingPluginIds).has('p1')).toBe(false);
	});

	it('tracks independent plugin ids independently', async () => {
		const { pluginStore, plugins, pendingPluginIds } = await freshStore();
		plugins.set([{ ...PLUGIN, id: 'p1' }, { ...PLUGIN, id: 'p2' }]);

		const gate1 = deferred<{ data: unknown }>();
		const gate2 = deferred<{ data: unknown }>();
		mockPost.mockReturnValueOnce(gate1.promise).mockReturnValueOnce(gate2.promise);

		const t1 = pluginStore.togglePlugin('p1', true);
		const t2 = pluginStore.togglePlugin('p2', true);

		expect(get(pendingPluginIds)).toEqual(new Set(['p1', 'p2']));

		gate1.resolve({ data: { success: true } });
		await t1;
		expect(get(pendingPluginIds)).toEqual(new Set(['p2']));

		gate2.resolve({ data: { success: true } });
		await t2;
		expect(get(pendingPluginIds).size).toBe(0);
	});

	it('updatePluginSettings also uses pendingPluginIds instead of loading', async () => {
		const { pluginStore, loading, pendingPluginIds } = await freshStore();
		const gate = deferred<{ data: unknown }>();
		mockPut.mockReturnValueOnce(gate.promise);

		const savePromise = pluginStore.updatePluginSettings('p1', { key: 'value' });

		expect(get(pendingPluginIds).has('p1')).toBe(true);
		expect(get(loading)).toBe(false);

		gate.resolve({ data: { success: true } });
		await savePromise;

		expect(get(pendingPluginIds).has('p1')).toBe(false);
	});

	it('leaves the initial-load skeleton behavior on loading untouched', async () => {
		const { pluginStore, loading, pendingPluginIds } = await freshStore();
		const gate = deferred<{ data: unknown }>();
		mockGet.mockReturnValueOnce(gate.promise);

		const loadPromise = pluginStore.loadPlugins();

		expect(get(loading)).toBe(true);
		expect(get(pendingPluginIds).size).toBe(0);

		gate.resolve({ data: { success: true, data: [] } });
		await loadPromise;

		expect(get(loading)).toBe(false);
	});

	it('reset clears pendingPluginIds alongside the other stores', async () => {
		const { pluginStore, pendingPluginIds } = await freshStore();
		pendingPluginIds.set(new Set(['p1']));

		pluginStore.reset();

		expect(get(pendingPluginIds).size).toBe(0);
	});
});
