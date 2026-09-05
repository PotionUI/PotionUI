import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';

const mockGet = vi.fn();
vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: () => ({ get: mockGet }),
		getBaseURL: () => '',
		getToken: () => null,
		setOnAuthExpired: vi.fn()
	}
}));
vi.mock('$lib/components/plugins/PluginDistHost.svelte', () => ({ default: () => null }));

type Catalogs = {
	renderers?: any[];
	contributions?: any[];
	revisions?: Record<string, string>;
	fieldTypes?: any[];
	pages?: any[];
	quickActions?: any[];
	widgets?: any[];
	hooks?: Record<string, any[]>;
};

/** Answers the six catalogue endpoints one refresh fetches. */
function respondWith(catalogs: Catalogs) {
	const bodies: Record<string, unknown> = {
		'/api/plugins/frontend-extensions': {
			renderers: catalogs.renderers ?? [],
			contributions: catalogs.contributions ?? [],
			revisions: catalogs.revisions ?? {}
		},
		'/api/fields/types': catalogs.fieldTypes ?? [],
		'/api/plugins/pages': catalogs.pages ?? [],
		'/api/plugins/quick-actions': catalogs.quickActions ?? [],
		'/api/plugins/sidebar-widgets': catalogs.widgets ?? [],
		'/api/plugins/hooks/frontend': catalogs.hooks ?? {}
	};
	mockGet.mockImplementation(async (path: string) => ({
		data: { success: true, data: bodies[path] }
	}));
}

async function loadModules() {
	vi.resetModules();
	const [refresh, artifacts, workbench, fields, slots, plugins, messages] = await Promise.all([
		import('./extensionRefresh'),
		import('$lib/registries/artifactRendererRegistry'),
		import('$lib/registries/workbenchFileRendererRegistry'),
		import('$lib/fields/registry'),
		import('$lib/extensions/extensionSlots'),
		import('$lib/stores/plugins'),
		import('$lib/registries/generationMessageRegistry')
	]);
	return { ...refresh, ...artifacts, ...workbench, ...fields, ...slots, ...plugins, ...messages };
}

const STARS_FIELD = { type: 'stars', component: 'plugin:example:StarsField.js', source: 'plugin' };
const ARTIFACT_RENDERER = {
	plugin_id: 'example',
	kind: 'history.artifact',
	key: 'fake_artifact',
	component: 'FakeArtifact.js'
};

describe('refreshPluginExtensions', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('makes a newly enabled plugin visible across every catalogue', async () => {
		const m = await loadModules();
		respondWith({
			renderers: [ARTIFACT_RENDERER],
			fieldTypes: [{ type: 'text', component: 'TextInput', source: 'core' }, STARS_FIELD],
			contributions: [{ plugin_id: 'example', slot: 'admin.tabs', component: 'Tab.js', order: 10 }],
			pages: [{ plugin_id: 'example', route: '/x', component_path: 'Page.js', label: 'X' }],
			quickActions: [{ plugin_id: 'example', action_id: 'go', label: 'Go' }],
			widgets: [{ plugin_id: 'example', widget_id: 'w', component: 'W.js' }],
			hooks: { 'sidebar.footer': [{ plugin_id: 'example', sort_order: 2 }, { plugin_id: 'example', sort_order: 1 }] }
		});

		await m.refreshPluginExtensions();

		expect(m.artifactRendererRegistry.has('fake_artifact')).toBe(true);
		expect(m.hasFieldComponent('stars')).toBe(true);
		expect(get(m.contributionsForSlot('admin.tabs'))).toHaveLength(1);
		expect(get(m.pluginPages)).toHaveLength(1);
		expect(get(m.pluginQuickActions)).toHaveLength(1);
		expect(get(m.sidebarWidgets)).toHaveLength(1);
		expect(get(m.frontendHooks)['sidebar.footer'].map((h: any) => h.sort_order)).toEqual([1, 2]);
	});

	it('removes a disabled plugin from every catalogue on the next refresh', async () => {
		const m = await loadModules();
		respondWith({
			renderers: [ARTIFACT_RENDERER, { plugin_id: 'example', kind: 'generation.output', key: 'custom', component: 'C.js' }],
			fieldTypes: [STARS_FIELD],
			contributions: [{ plugin_id: 'example', slot: 'admin.tabs', component: 'Tab.js', order: 10 }],
			pages: [{ plugin_id: 'example', route: '/x', component_path: 'Page.js', label: 'X' }]
		});
		await m.refreshPluginExtensions();
		expect(m.artifactRendererRegistry.has('fake_artifact')).toBe(true);

		respondWith({});
		await m.refreshPluginExtensions();

		expect(m.artifactRendererRegistry.has('fake_artifact')).toBe(false);
		expect(m.hasFieldComponent('stars')).toBe(false);
		expect(m.generationMessageRegistry.has('custom')).toBe(false);
		expect(get(m.contributionsForSlot('admin.tabs'))).toHaveLength(0);
		expect(get(m.pluginPages)).toHaveLength(0);
	});

	it("leaves core's registration for a key a removed plugin had shadowed", async () => {
		const m = await loadModules();
		const CoreImage = { core: true };
		m.registerWorkbenchFileRenderer('image', { component: CoreImage });

		respondWith({
			renderers: [{ plugin_id: 'example', kind: 'workbench.file', key: 'image', component: 'FancyImage.js' }]
		});
		await m.refreshPluginExtensions();
		respondWith({});
		await m.refreshPluginExtensions();

		expect(m.hasWorkbenchFileRenderer('image')).toBe(true);
		await expect(m.resolveWorkbenchFileRenderer('image')).resolves.toBe(CoreImage);
	});

	it('leaves another plugin’s registration for the same key in place', async () => {
		const m = await loadModules();
		respondWith({
			renderers: [
				{ plugin_id: 'a', kind: 'history.artifact', key: 'shared', component: 'A.js' },
				{ plugin_id: 'b', kind: 'history.artifact', key: 'shared', component: 'B.js' },
				{ plugin_id: 'b', kind: 'history.artifact', key: 'only_b', component: 'OnlyB.js' }
			]
		});
		await m.refreshPluginExtensions();

		respondWith({
			renderers: [
				{ plugin_id: 'b', kind: 'history.artifact', key: 'shared', component: 'B.js' },
				{ plugin_id: 'b', kind: 'history.artifact', key: 'only_b', component: 'OnlyB.js' }
			]
		});
		await m.refreshPluginExtensions();

		expect(m.artifactRendererRegistry.has('shared')).toBe(true);
		expect(m.artifactRendererRegistry.has('only_b')).toBe(true);
	});

	it('retries after a failed initial request instead of latching', async () => {
		const m = await loadModules();
		mockGet.mockRejectedValue(new Error('network error'));

		await expect(m.refreshPluginExtensions()).resolves.toBeUndefined();
		expect(m.artifactRendererRegistry.has('fake_artifact')).toBe(false);

		respondWith({ renderers: [ARTIFACT_RENDERER] });
		await m.refreshPluginExtensions();

		expect(m.artifactRendererRegistry.has('fake_artifact')).toBe(true);
	});

	it('keeps the previously applied snapshot when one catalogue fails', async () => {
		const m = await loadModules();
		respondWith({ renderers: [ARTIFACT_RENDERER], fieldTypes: [STARS_FIELD] });
		await m.refreshPluginExtensions();

		mockGet.mockImplementation(async (path: string) => {
			if (path === '/api/fields/types') throw new Error('boom');
			return { data: { success: true, data: { renderers: [], contributions: [], revisions: {} } } };
		});
		await m.refreshPluginExtensions();

		expect(m.artifactRendererRegistry.has('fake_artifact')).toBe(true);
		expect(m.hasFieldComponent('stars')).toBe(true);
	});

	it('coalesces overlapping refreshes into one in-flight request at a time', async () => {
		const m = await loadModules();
		respondWith({ renderers: [ARTIFACT_RENDERER] });

		const first = m.refreshPluginExtensions();
		const second = m.refreshPluginExtensions();
		const third = m.refreshPluginExtensions();
		await Promise.all([first, second, third]);

		expect(second).toBe(third);
		// Six endpoints per run: the three calls produce the in-flight run plus
		// one follow-up, never three runs.
		expect(mockGet.mock.calls).toHaveLength(12);
		expect(m.artifactRendererRegistry.has('fake_artifact')).toBe(true);
	});
});
