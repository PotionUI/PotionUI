import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

const mockGet = vi.fn();
vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: () => ({ get: mockGet }),
		getBaseURL: () => '',
		getToken: () => null,
		setOnAuthExpired: vi.fn(),
		setOnTokenRefreshed: vi.fn()
	}
}));
vi.mock('$lib/components/plugins/PluginDistHost.svelte', () => ({ default: () => null }));

type Editor = Record<string, unknown[]>;

function respondWith(extensions: Record<string, unknown>) {
	const bodies: Record<string, unknown> = {
		'/api/plugins/frontend-extensions': { renderers: [], contributions: [], revisions: { acme: 'r1' }, tool_tabs: [], ...extensions },
		'/api/fields/types': [],
		'/api/plugins/pages': [],
		'/api/plugins/quick-actions': [],
		'/api/plugins/sidebar-widgets': [],
		'/api/plugins/hooks/frontend': {}
	};
	mockGet.mockImplementation(async (path: string) => ({ data: { success: true, data: bodies[path] } }));
}

async function loadModules() {
	vi.resetModules();
	const [refresh, editors] = await Promise.all([import('$lib/plugin-api/extensionRefresh'), import('$lib/editors')]);
	return { ...refresh, ...editors };
}

const EDITOR: Editor = {
	editor_groups: [{ plugin_id: 'acme', id: 'acme.stylize', editor: 'image', label: 'Stylize', order: 450 }],
	editor_tools: [{ plugin_id: 'acme', id: 'acme.halftone', editor: 'image', group: 'acme.stylize', label: 'Halftone', options: 'editor/Options.js' }],
	editor_overlays: [{ plugin_id: 'acme', id: 'acme.pick', editor: 'image', component: 'editor/Pick.js', produces: 'selection' }],
	editor_tasks: [{ plugin_id: 'acme', id: 'acme.relight', label: 'Relight', editors: ['image'], region: 'optional', prompt: 'optional', tool: 'acme.halftone' }]
};

describe('editor entries in the frontend extensions snapshot', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('registers the entries that are present', async () => {
		const m = await loadModules();
		respondWith(EDITOR);
		await m.refreshPluginExtensions();
		expect(m.railGroups.get('image', 'acme.stylize')?.pluginId).toBe('acme');
		expect(m.railTools.get('image', 'acme.halftone')?.options).toEqual({ pluginId: 'acme', path: 'editor/Options.js' });
		expect(m.overlays.get('image', 'acme.pick')?.produces).toBe('selection');
		expect(m.tasks.get('image', 'acme.relight')?.tool).toBe('acme.halftone');
	});

	it('removes them when the plugin is disabled or removed, and keeps a plugin that is still there', async () => {
		const m = await loadModules();
		respondWith(EDITOR);
		await m.refreshPluginExtensions();
		respondWith({ editor_groups: EDITOR.editor_groups });
		await m.refreshPluginExtensions();
		expect(m.railGroups.get('image', 'acme.stylize')).toBeDefined();
		expect(m.railTools.get('image', 'acme.halftone')).toBeUndefined();
		expect(m.overlays.get('image', 'acme.pick')).toBeUndefined();
		expect(m.tasks.get('image', 'acme.relight')).toBeUndefined();
		respondWith({});
		await m.refreshPluginExtensions();
		expect(m.railGroups.get('image', 'acme.stylize')).toBeUndefined();
	});

	it('leaves an unchanged entry alone on the next refresh: no revision bump and the failure flag stays', async () => {
		const m = await loadModules();
		respondWith(EDITOR);
		await m.refreshPluginExtensions();
		m.reportFailure('image:acme.halftone', 'render', new Error('boom'));
		await Promise.resolve();
		const before = get(m.editorsRevision);
		const entry = m.railTools.get('image', 'acme.halftone');
		await m.refreshPluginExtensions();
		await m.refreshPluginExtensions();
		expect(get(m.editorsRevision)).toBe(before);
		expect(m.railTools.get('image', 'acme.halftone')).toBe(entry);
		expect(m.hasFailed('image:acme.halftone')).toBe(true);
	});

	it('replaces an entry whose declaration or plugin revision changed', async () => {
		const m = await loadModules();
		respondWith(EDITOR);
		await m.refreshPluginExtensions();
		respondWith({ ...EDITOR, editor_tools: [{ ...(EDITOR.editor_tools![0] as object), label: 'Halftone 2' }] });
		await m.refreshPluginExtensions();
		expect(m.railTools.get('image', 'acme.halftone')?.label).toBe('Halftone 2');
		respondWith({ ...EDITOR, revisions: { acme: 'r2' }, editor_tools: [{ ...(EDITOR.editor_tools![0] as object), label: 'Halftone 2' }] });
		const before = get(m.editorsRevision);
		await m.refreshPluginExtensions();
		expect(get(m.editorsRevision)).toBeGreaterThan(before);
	});

	it('ends runtime registrations with their plugin and brings them back when it returns', async () => {
		const m = await loadModules();
		respondWith({});
		await m.refreshPluginExtensions();
		const editors = m.createHostEditorsApi();
		editors.registerRailTool({ id: 'acme.rt', editor: 'image', group: 'tools', label: 'Runtime', icon: 'x' }, { pluginId: 'acme' });
		const off = editors.registerRailTool({ id: 'acme.gone', editor: 'image', group: 'tools', label: 'Gone', icon: 'x' }, { pluginId: 'acme' });
		off();
		respondWith({ revisions: {} });
		await m.refreshPluginExtensions();
		expect(m.railTools.get('image', 'acme.rt')).toBeUndefined();
		respondWith({ revisions: { acme: 'r1' } });
		await m.refreshPluginExtensions();
		expect(m.railTools.get('image', 'acme.rt')?.label).toBe('Runtime');
		expect(m.railTools.get('image', 'acme.gone')).toBeUndefined();
	});

	it('works with a server that sends none of the keys, and flips the ready flag after the first refresh', async () => {
		const m = await loadModules();
		respondWith({});
		expect(get(m.editorExtensionsReady)).toBe(false);
		await expect(m.refreshPluginExtensions()).resolves.toBeUndefined();
		expect(m.railTools.list('image')).toEqual([]);
		expect(get(m.editorExtensionsReady)).toBe(true);
	});

	it('flips the ready flag even when the first refresh fails, so rows stop saying Loading', async () => {
		const m = await loadModules();
		mockGet.mockRejectedValue(new Error('offline'));
		await m.refreshPluginExtensions();
		expect(get(m.editorExtensionsReady)).toBe(true);
	});

	it('reads the server form: all five keys present, unset fields as null', async () => {
		const m = await loadModules();
		respondWith({
			editor_steps: [{ plugin_id: 'acme', id: 'acme.halftone', editor: 'image', module: 'editor/halftone.js', label: 'Halftone', icon: 'extension', lane: null }],
			editor_tools: [
				{ plugin_id: 'acme', id: 'acme.halftone', editor: 'image', group: 'look', label: 'Halftone', icon: 'extension', order: null, keys: null, overlay: null, task: null, options: 'editor/Options.js', bar: null }
			],
			editor_groups: [{ plugin_id: 'acme', id: 'acme.stylize', editor: 'image', label: 'Stylize', order: 500 }],
			editor_overlays: [{ plugin_id: 'acme', id: 'acme.pick', editor: 'image', component: 'editor/Pick.js', cursor: null, produces: null }],
			editor_tasks: [
				{ plugin_id: 'acme', id: 'acme.relight', editors: ['image'], label: 'Relight', icon: 'extension', region: 'optional', prompt: 'optional', produces: 'patch', tool: null, step_kind: null, fallback: null }
			]
		});
		await m.refreshPluginExtensions();
		const tool = m.railTools.get('image', 'acme.halftone')!;
		expect(tool.options).toEqual({ pluginId: 'acme', path: 'editor/Options.js' });
		expect(tool.order).toBeUndefined();
		expect(tool.keys).toBeUndefined();
		expect(tool.overlay).toBeUndefined();
		expect(tool.bar).toBeUndefined();
		expect(m.railGroups.get('image', 'acme.stylize')?.order).toBe(500);
		expect(m.stepKinds.get('image', 'acme.halftone')?.lane).toBeUndefined();
		expect(m.overlays.get('image', 'acme.pick')?.cursor).toBeUndefined();
		const task = m.tasks.get('image', 'acme.relight')!;
		expect(task.stepKind).toBe('generate');
		expect(task.fallback).toBeUndefined();
	});

	it('refuses an entry whose id does not carry the plugin id', async () => {
		const m = await loadModules();
		respondWith({ editor_tools: [{ plugin_id: 'acme', id: 'halftone', editor: 'image', group: 'tools', label: 'Halftone' }] });
		await m.refreshPluginExtensions();
		expect(m.railTools.get('image', 'halftone')).toBeUndefined();
	});
});
