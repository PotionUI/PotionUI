import { describe, expect, it, vi } from 'vitest';
import { createHostEditorsApi, declaredStepKind, describeEditorEntries, emptyEditorSnapshot, readEditorSnapshot, type EditorSnapshot } from './pluginEntries';
import { overlays } from './overlays';
import { railGroups, railTools } from './railTools';
import { stepKinds, type StepContext, type StepKind } from './stepKinds';
import { tasks } from './tasks';
import type { EditStep } from '$lib/tool-tabs/frame/editStack';

vi.mock('$lib/services/api/index', () => ({ api: { getBaseURL: () => 'http://api.test' } }));
vi.mock('$lib/components/plugins/PluginDistHost.svelte', () => ({ default: () => null }));
vi.mock('$lib/utils/logger', () => ({ logger: { error: vi.fn(), warn: vi.fn(), debug: vi.fn() } }));

const revisionOf = () => 'r1';

function snapshot(): EditorSnapshot {
	return {
		menu: [],
		groups: [{ plugin_id: 'acme', id: 'acme.stylize', editor: 'image', label: 'Stylize', order: 450 }],
		steps: [{ plugin_id: 'acme', id: 'acme.halftone', editor: 'image', module: 'editor/halftone.js', label: 'Halftone', icon: 'extension', lane: 'paint' }],
		tools: [
			{ plugin_id: 'acme', id: 'acme.halftone', editor: 'image', group: 'acme.stylize', label: 'Halftone', options: 'editor/HalftoneOptions.js', order: 5 }
		],
		overlays: [{ plugin_id: 'acme', id: 'acme.magic-select', editor: 'image', component: 'editor/MagicSelect.js', produces: 'selection' }],
		tasks: [{ plugin_id: 'acme', id: 'acme.relight', label: 'Relight', editors: ['image', 'video'], region: 'optional', prompt: 'optional', tool: 'acme.halftone' }]
	};
}

function apply(descriptors: ReturnType<typeof describeEditorEntries>) {
	descriptors.forEach((descriptor) => descriptor.register());
	return () => descriptors.forEach((descriptor) => descriptor.dispose());
}

describe('readEditorSnapshot', () => {
	it('reads the six keys and tolerates all of them being absent', () => {
		expect(readEditorSnapshot(undefined)).toEqual(emptyEditorSnapshot());
		expect(readEditorSnapshot({ renderers: [] })).toEqual(emptyEditorSnapshot());
		expect(readEditorSnapshot({ editor_steps: 'nope', editor_tools: null })).toEqual(emptyEditorSnapshot());
		const read = readEditorSnapshot({
			editor_steps: [1],
			editor_tools: [2],
			editor_groups: [3],
			editor_overlays: [4],
			editor_tasks: [5],
			editor_menu: [6]
		});
		expect(Object.values(read).map((list) => list.length)).toEqual([1, 1, 1, 1, 1, 1]);
	});
});

describe('describeEditorEntries', () => {
	it('registers every entry under the plugin and removes them all when the plugin goes away', () => {
		const dispose = apply(describeEditorEntries(snapshot(), revisionOf));
		expect(railGroups.get('image', 'acme.stylize')?.pluginId).toBe('acme');
		expect(stepKinds.get('image', 'acme.halftone')?.lane).toBe('paint');
		expect(railTools.get('image', 'acme.halftone')?.options).toEqual({ pluginId: 'acme', path: 'editor/HalftoneOptions.js' });
		expect(overlays.get('image', 'acme.magic-select')?.produces).toBe('selection');
		expect(tasks.get('image', 'acme.relight')?.stepKind).toBe('generate');
		expect(tasks.get('video', 'acme.relight')?.label).toBe('Relight');
		dispose();
		expect(railGroups.get('image', 'acme.stylize')).toBeUndefined();
		expect(stepKinds.get('image', 'acme.halftone')).toBeUndefined();
		expect(railTools.get('image', 'acme.halftone')).toBeUndefined();
		expect(overlays.get('image', 'acme.magic-select')).toBeUndefined();
		expect(tasks.get('image', 'acme.relight')).toBeUndefined();
		expect(tasks.get('video', 'acme.relight')).toBeUndefined();
	});

	it('keeps a declared step kind registered when the same snapshot is applied again, and drops it on disposal', () => {
		const first = describeEditorEntries(snapshot(), revisionOf);
		first.forEach((descriptor) => descriptor.register());
		const second = describeEditorEntries(snapshot(), revisionOf);
		second.forEach((descriptor) => descriptor.register());
		expect(stepKinds.get('image', 'acme.halftone')?.label).toBe('Halftone');
		expect(stepKinds.list('image').filter((entry) => entry.id === 'acme.halftone')).toHaveLength(1);
		second.forEach((descriptor) => descriptor.dispose());
		expect(stepKinds.get('image', 'acme.halftone')).toBeUndefined();
	});

	it('gives a different identity when an entry or the plugin revision changes, and the same one otherwise', () => {
		const ids = (descriptors: ReturnType<typeof describeEditorEntries>) => descriptors.map((descriptor) => descriptor.id);
		const base = ids(describeEditorEntries(snapshot(), revisionOf));
		expect(ids(describeEditorEntries(snapshot(), revisionOf))).toEqual(base);
		expect(ids(describeEditorEntries(snapshot(), () => 'r2'))).not.toEqual(base);
		const changed = snapshot();
		changed.tools[0].label = 'Halftone 2';
		expect(ids(describeEditorEntries(changed, revisionOf))).not.toEqual(base);
	});

	it('registers nothing for an empty snapshot and skips entries with no plugin or id', () => {
		expect(describeEditorEntries(emptyEditorSnapshot(), revisionOf)).toEqual([]);
		const broken = snapshot();
		broken.tools = [{ ...broken.tools[0], plugin_id: '' }, { ...broken.tools[0], id: '' }];
		expect(describeEditorEntries(broken, revisionOf).filter((descriptor) => descriptor.id.startsWith('editor_tool'))).toEqual([]);
	});

	it('refuses an id that does not start with the plugin id', () => {
		const wrong = snapshot();
		wrong.tools[0].id = 'other.halftone';
		const dispose = apply(describeEditorEntries({ ...emptyEditorSnapshot(), tools: wrong.tools }, revisionOf));
		expect(railTools.get('image', 'other.halftone')).toBeUndefined();
		dispose();
	});

	it('registers a declared step kind that is busy until its module loads', async () => {
		let finish: (module: Partial<StepKind>) => void = () => undefined;
		const loader = () => new Promise<Partial<StepKind>>((resolve) => (finish = resolve));
		const declared = declaredStepKind(snapshot().steps[0], loader);
		const done = vi.fn();
		declared.start(done);
		const step = { id: 's1', tool: 'acme.halftone', icon: '', label: 'Halftone', summary: 'stored', params: {}, hidden: false } as EditStep;
		const ctx = { step, input: null, beneath: [], catalog: [], editor: 'image' } as StepContext;
		expect(declared.kind.state?.(step, ctx)).toEqual({ kind: 'busy', label: 'Loading' });
		expect(declared.kind.payload({}, ctx)).toEqual([{ tool: 'acme.halftone', params: {}, hidden: true }]);
		finish({ version: 2, payload: () => [{ tool: 'acme_halftone', params: { v: 1 } }] });
		await Promise.resolve();
		await Promise.resolve();
		expect(done).toHaveBeenCalledTimes(1);
		const next = done.mock.calls[0][0] as StepKind;
		expect(next.version).toBe(2);
		expect(declared.kind.state?.(step, ctx)).toEqual({ kind: 'done' });
		expect(declared.kind.payload({}, ctx)).toEqual([{ tool: 'acme_halftone', params: { v: 1 } }]);
	});

	it('marks a declared step kind failed when its module cannot be loaded', async () => {
		const declared = declaredStepKind(snapshot().steps[0], () => Promise.reject(new Error('404')));
		const done = vi.fn();
		declared.start(done);
		await Promise.resolve();
		await Promise.resolve();
		expect(done).toHaveBeenCalledWith(null);
		const step = { id: 's1', tool: 'acme.halftone', icon: '', label: 'H', summary: '', params: {}, hidden: false } as EditStep;
		expect(declared.kind.state?.(step, { step } as StepContext)).toMatchObject({ kind: 'skipped', tone: 'danger' });
	});
});

describe('the host namespace', () => {
	it('registers for a plugin, returns an unregister function and exposes get and list', () => {
		const editors = createHostEditorsApi();
		const dispose = editors.registerRailGroup({ id: 'acme.runtime', editor: 'image', label: 'Runtime', order: 1 }, { pluginId: 'acme' });
		expect(editors.railGroups.get('image', 'acme.runtime')?.label).toBe('Runtime');
		expect(editors.railGroups.list('image').some((entry) => entry.id === 'acme.runtime')).toBe(true);
		dispose();
		expect(editors.railGroups.get('image', 'acme.runtime')).toBeUndefined();
	});

	it('refuses an unprefixed id and a call with no plugin id', () => {
		const editors = createHostEditorsApi();
		editors.registerRailGroup({ id: 'runtime', editor: 'image', label: 'Runtime', order: 1 }, { pluginId: 'acme' });
		editors.registerRailGroup({ id: 'acme.runtime', editor: 'image', label: 'Runtime', order: 1 }, {} as never);
		expect(editors.railGroups.get('image', 'runtime')).toBeUndefined();
		expect(editors.railGroups.get('image', 'acme.runtime')).toBeUndefined();
	});

	it('covers step kinds, tools, overlays and tasks', () => {
		const editors = createHostEditorsApi();
		const disposers = [
			editors.registerStepKind({ id: 'acme.a', editor: 'image', label: 'A', icon: 'x', summary: () => '', payload: () => [] }, { pluginId: 'acme' }),
			editors.registerRailTool({ id: 'acme.a', editor: 'image', group: 'tools', label: 'A', icon: 'x' }, { pluginId: 'acme' }),
			editors.registerOverlay({ id: 'acme.o', editor: 'image', component: { pluginId: 'acme', path: 'o.js' } }, { pluginId: 'acme' }),
			editors.registerTask({ id: 'acme.t', editor: 'image', label: 'T', icon: 'x', tool: 'acme.a', region: 'none', prompt: 'none', stepKind: 'generate' }, { pluginId: 'acme' })
		];
		expect(editors.stepKinds.get('image', 'acme.a')).toBeDefined();
		expect(editors.railTools.get('image', 'acme.a')).toBeDefined();
		expect(editors.overlays.get('image', 'acme.o')).toBeDefined();
		expect(editors.tasks.get('image', 'acme.t')).toBeDefined();
		disposers.forEach((dispose) => dispose());
		expect(editors.stepKinds.get('image', 'acme.a')).toBeUndefined();
	});

	it('registers a menu item for a plugin, exposes it on menuItems and refuses a foreign id', () => {
		const editors = createHostEditorsApi();
		const entry = { id: 'acme.menu', editor: 'image' as const, menu: 'tools', group: 'acme.g', label: 'Menu', icon: 'x', run: () => undefined };
		const dispose = editors.registerMenuItem(entry, { pluginId: 'acme' });
		expect(editors.menuItems.get('image', 'acme.menu')?.pluginId).toBe('acme');
		expect(editors.menuItems.list('image').some((item) => item.id === 'acme.menu')).toBe(true);
		dispose();
		expect(editors.menuItems.get('image', 'acme.menu')).toBeUndefined();
		const refused = editors.registerMenuItem({ ...entry, id: 'other.menu' }, { pluginId: 'acme' });
		expect(editors.menuItems.get('image', 'other.menu')).toBeUndefined();
		refused();
	});
});
