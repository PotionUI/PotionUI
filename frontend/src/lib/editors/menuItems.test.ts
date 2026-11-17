import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/utils/logger', () => ({ logger: { error: vi.fn(), warn: vi.fn(), debug: vi.fn() } }));

import { pluginOwner } from './registry';
import { resetFailures, hasFailed } from './guard';
import { buildMenu, buildMenus, flatRows, menuItems, menuTabs, type EditorMenuCommands, type EditorMenuContext, type MenuItemEntry } from './menuItems';
import { ensureCoreEditorMenu } from './coreMenu';
import { declaredMenuItem, describeEditorEntries, emptyEditorSnapshot } from './pluginEntries';

vi.mock('$lib/services/api/index', () => ({ api: { getBaseURL: () => 'http://api.test' } }));
vi.mock('$lib/components/plugins/PluginDistHost.svelte', () => ({ default: () => null }));

function commands(log: string[]): EditorMenuCommands {
	const make = (name: string) => () => void log.push(name);
	return {
		save: (action) => void log.push(`save:${action.kind}${'target' in action ? `:${action.target}` : ''}`),
		closeEditor: make('close'),
		replaceWith: (id) => void log.push(`replace:${id}`),
		openRecent: (id) => void log.push(`recent:${id}`),
		openAllEdits: make('all-edits'),
		undo: make('undo'),
		redo: make('redo'),
		apply: make('apply'),
		reset: make('reset'),
		toggleStepHidden: make('hide'),
		removeStep: make('remove-step'),
		removeAll: make('remove-all'),
		toggleCompare: make('compare'),
		zoomIn: make('zoom-in'),
		zoomOut: make('zoom-out'),
		fit: make('fit'),
		actualSize: make('actual'),
		toggleEdits: make('edits'),
		selectTool: (id) => void log.push(`tool:${id}`)
	};
}

function context(log: string[] = [], patch: Partial<EditorMenuContext> = {}): EditorMenuContext {
	return {
		editor: 'image',
		hasSource: true,
		phone: false,
		busy: false,
		tool: 'crop',
		rail: [
			{ id: 'shape', label: 'Shape', tools: [{ id: 'crop', label: 'Crop', icon: 'editor-crop' }, { id: 'resize', label: 'Resize', icon: 'editor-resize' }] },
			{ id: 'look', label: 'Look', tools: [{ id: 'effects', label: 'Looks', icon: 'editor-effects' }] }
		],
		saved: false,
		canSave: true,
		replace: { allowed: false, reason: 'Files from History stay as generated' },
		sendTargets: [{ id: 't1', name: 'Generation 1', detail: 'Qwen · Source', badge: 'MASK' }],
		newGeneration: false,
		downloadFormat: 'PNG',
		copyLabel: 'Copy image',
		sources: [
			{ id: 'history', label: 'History', icon: 'clock' },
			{ id: 'library', label: 'Library', icon: 'photo' }
		],
		currentSource: null,
		replaceLabel: 'Replace picture',
		replaceBlocked: false,
		recentEdits: [{ id: 'e1', label: 'dockside' }],
		canUndo: true,
		undoLabel: 'Matte',
		canRedo: false,
		redoLabel: null,
		apply: { label: 'Apply crop', enabled: true },
		reset: { label: 'Reset', icon: 'rotate', enabled: true },
		step: { id: 's1', label: 'Matte', hidden: false },
		editCount: 2,
		compare: false,
		canCompare: true,
		zoom: { fit: true, actual: false, canIn: true, canOut: true },
		editsShown: true,
		commands: commands(log),
		...patch
	};
}

function row(menu: string, label: string, ctx: EditorMenuContext) {
	return flatRows(buildMenu(menu, ctx)).find((entry) => entry.label === label);
}

beforeEach(() => {
	resetFailures();
	ensureCoreEditorMenu();
});

describe('the core menu', () => {
	it('lists the four menus in order', () => {
		expect(menuTabs('image').map((tab) => tab.label)).toEqual(['File', 'Edit', 'View', 'Tools']);
		expect(buildMenus(context()).map((menu) => menu.id)).toEqual(['file', 'edit', 'view', 'tools']);
	});

	it('orders the File items and separates them into groups', () => {
		const menu = buildMenu('file', context());
		expect(menu.groups.map((group) => group.map((entry) => entry.label))).toEqual([
			['Replace picture', 'Recent edits'],
			['Save as new', 'Replace original'],
			['Send to', 'Download', 'Copy image'],
			['Close']
		]);
	});

	it('shows Save before Save as new once a copy exists, and the plus icon on Save as new', () => {
		const menu = buildMenu('file', context([], { saved: true, replace: { allowed: true } }));
		const save = menu.groups[1];
		expect(save.map((entry) => entry.label)).toEqual(['Save', 'Save as new', 'Replace original']);
		expect(save[0].keybind).toBe('Ctrl S');
		expect(save[1].keybind).toBeUndefined();
		expect(save[1].icon).toBe('plus');
	});

	it('orders the Edit and View items with the step name on Undo', () => {
		expect(buildMenu('edit', context()).groups.map((group) => group.map((entry) => entry.label))).toEqual([
			['Undo Matte', 'Redo'],
			['Apply crop', 'Reset'],
			['Hide step', 'Remove step'],
			['Remove all edits']
		]);
		expect(buildMenu('view', context()).groups.map((group) => group.map((entry) => entry.label))).toEqual([
			['Before / after'],
			['Zoom in', 'Zoom out', 'Fit', 'Actual size 1:1'],
			['Hide edits']
		]);
	});

	it('swaps the labels for Show step and Show edits', () => {
		const ctx = context([], { step: { id: 's1', label: 'Matte', hidden: true }, editsShown: false });
		expect(row('edit', 'Show step', ctx)?.icon).toBe('editor-eye');
		expect(row('view', 'Show edits', ctx)).toBeDefined();
	});

	it('gives each unavailable item a plain reason and leaves the available ones empty', () => {
		const ctx = context([], { canRedo: false, canUndo: false, step: null, editCount: 0, canCompare: false });
		expect(row('edit', 'Redo', ctx)?.reason).toBe('Nothing to redo');
		expect(row('edit', 'Undo Matte', ctx)?.reason).toBe('Nothing to undo');
		expect(row('edit', 'Hide step', ctx)?.reason).toBe('Select an edit first');
		expect(row('edit', 'Remove all edits', ctx)?.reason).toBe('There are no edits to remove');
		expect(row('view', 'Before / after', ctx)?.reason).toBe('Make an edit to compare');
		expect(row('edit', 'Undo Matte', context())?.reason).toBeNull();
	});

	it('carries the real Replace original reason and the save gate', () => {
		expect(row('file', 'Replace original', context())?.reason).toBe('Files from History stay as generated');
		expect(row('file', 'Replace original', context([], { replace: { allowed: true }, canSave: false }))?.reason).toBe('Nothing to save yet');
		expect(row('file', 'Replace original', context([], { replace: { allowed: true } }))?.reason).toBeNull();
	});

	it('does not run an unavailable item', () => {
		const log: string[] = [];
		row('edit', 'Redo', context(log))?.run();
		row('file', 'Replace original', context(log))?.run();
		expect(log).toEqual([]);
	});

	it('runs the action each item is wired to', () => {
		const log: string[] = [];
		const ctx = context(log, { saved: true, replace: { allowed: true }, canRedo: true, redoLabel: 'Crop' });
		const run = (menu: string, label: string) => row(menu, label, ctx)!.run();
		run('file', 'Save');
		run('file', 'Save as new');
		run('file', 'Replace original');
		run('file', 'Download');
		run('file', 'Copy image');
		run('file', 'Close');
		run('edit', 'Undo Matte');
		run('edit', 'Redo Crop');
		run('edit', 'Apply crop');
		run('edit', 'Reset');
		run('edit', 'Hide step');
		run('edit', 'Remove step');
		run('edit', 'Remove all edits');
		run('view', 'Before / after');
		run('view', 'Zoom in');
		run('view', 'Zoom out');
		run('view', 'Fit');
		run('view', 'Actual size 1:1');
		run('view', 'Hide edits');
		run('tools', 'Resize');
		expect(log).toEqual([
			'save:save', 'save:save-new', 'save:replace', 'save:download', 'save:copy', 'close',
			'undo', 'redo', 'apply', 'reset', 'hide', 'remove-step', 'remove-all',
			'compare', 'zoom-in', 'zoom-out', 'fit', 'actual', 'edits', 'tool:resize'
		]);
	});

	it('builds the submenus from the context', () => {
		const log: string[] = [];
		const ctx = context(log, { newGeneration: true });
		const replace = row('file', 'Replace picture', ctx)!;
		expect(replace.children?.map((child) => child.id)).toEqual(['history', 'library']);
		(replace.children![0] as { run(): void }).run();
		const recent = row('file', 'Recent edits', ctx)!;
		expect(recent.children?.map((child) => child.id)).toEqual(['e1', 'recent-sep', 'recent-all']);
		(recent.children![0] as { run(): void }).run();
		(recent.children!.at(-1) as { run(): void }).run();
		const send = row('file', 'Send to', ctx)!;
		expect(send.children?.map((child) => child.id)).toEqual(['send-head', 't1', 'new-generation']);
		(send.children![1] as { run(): void }).run();
		(send.children![2] as { run(): void }).run();
		expect(log).toEqual(['replace:history', 'recent:e1', 'all-edits', 'save:send:t1', 'save:new-generation']);
	});

	it('shows a loading row and an empty row for the recent edits', () => {
		const loading = row('file', 'Recent edits', context([], { recentEdits: null }))!;
		expect(loading.children?.[0]).toMatchObject({ id: 'recent-loading', disabled: expect.any(String) });
		const none = row('file', 'Recent edits', context([], { recentEdits: [] }))!;
		expect(none.children?.[0]).toMatchObject({ id: 'recent-none' });
	});

	it('hides Send to when there is nowhere to send and Recent edits outside the image editor', () => {
		expect(row('file', 'Send to', context([], { sendTargets: [] }))).toBeUndefined();
		expect(row('file', 'Recent edits', context([], { editor: 'video' }))).toBeUndefined();
		expect(row('file', 'Replace picture', context([], { hasSource: false }))).toBeUndefined();
	});

	it('hides the zoom group on a phone and disables it when no canvas is mounted', () => {
		expect(row('view', 'Zoom in', context([], { phone: true }))).toBeUndefined();
		expect(row('view', 'Zoom in', context([], { zoom: null }))?.reason).toBe('Zoom is not available here');
	});

	it('marks the checked items', () => {
		const ctx = context([], { compare: true, zoom: { fit: false, actual: true, canIn: true, canOut: true } });
		expect(row('view', 'Before / after', ctx)?.checked).toBe(true);
		expect(row('view', 'Fit', ctx)?.checked).toBe(false);
		expect(row('view', 'Actual size 1:1', ctx)?.checked).toBe(true);
		expect(row('edit', 'Redo', ctx)?.checked).toBeNull();
	});

	it('gives the same items to the video editor with its own labels', () => {
		const ctx = context([], { editor: 'video', replaceLabel: 'Replace clip', copyLabel: 'Copy link', downloadFormat: 'MP4' });
		const file = flatRows(buildMenu('file', ctx)).map((entry) => entry.label);
		expect(file).toContain('Replace clip');
		expect(file).toContain('Copy link');
		expect(file).not.toContain('Recent edits');
	});
});

describe('the Tools menu', () => {
	it('mirrors the rail in its groups and marks the current tool', () => {
		const menu = buildMenu('tools', context());
		expect(menu.groups.map((group) => group.map((entry) => entry.label))).toEqual([['Crop', 'Resize'], ['Looks']]);
		expect(flatRows(menu).map((entry) => entry.checked)).toEqual([true, false, false]);
		expect(menu.groups[0][0].id).toBe('tool:crop');
	});

	it('with grouped tools lists single entries as items and multi-entry groups as submenus, whatever the rail folded', () => {
		const log: string[] = [];
		const ctx = context(log, {
			groupedTools: true,
			tool: 'vignette',
			appliedTools: ['light', 'vignette'],
			rail: [
				{ id: 'shape', label: 'Transform', tools: [{ id: 'crop', label: 'Crop', icon: 'a' }, { id: 'resize', label: 'Resize', icon: 'b' }] },
				{ id: 'paint', label: 'Paint', tools: [{ id: 'paint', label: 'Paint', icon: 'c' }] },
				{ id: 'fx', label: 'Effects', tools: [{ id: 'film_grain', label: 'Film grain', icon: 'd' }, { id: 'vignette', label: 'Vignette', icon: 'e' }] }
			]
		});
		const menu = buildMenu('tools', ctx);
		const rows = flatRows(menu);
		expect(rows.map((entry) => entry.label)).toEqual(['Transform', 'Paint', 'Effects']);
		expect(rows.map((entry) => entry.children?.length ?? 0)).toEqual([2, 0, 2]);
		const effects = rows[2].children!.filter((child) => 'run' in child);
		expect(effects.map((child) => [child.id, (child as { checked?: boolean }).checked, !!(child as { dot?: string }).dot])).toEqual([
			['tool:film_grain', false, false],
			['tool:vignette', true, true]
		]);
		(effects[0] as { run(): void }).run();
		expect(log).toEqual(['tool:film_grain']);
	});

	it('carries a disabled rail tool with its reason', () => {
		const ctx = context([], {
			rail: [{ id: 'shape', label: 'Shape', tools: [{ id: 'crop', label: 'Crop', icon: 'x', disabled: true, reason: 'Open a picture first' }] }]
		});
		expect(row('tools', 'Crop', ctx)?.reason).toBe('Open a picture first');
	});
});

describe('plugin entries', () => {
	const entry = (patch: Partial<MenuItemEntry> = {}): MenuItemEntry => ({
		id: 'acme.note',
		editor: 'image',
		menu: 'tools',
		group: 'acme.group',
		label: 'Note',
		icon: 'extension',
		run: vi.fn(),
		...patch
	});

	it('adds an item to a core menu after the core groups and removes it on disposal', () => {
		const dispose = menuItems.register(entry(), pluginOwner('acme'));
		const groups = buildMenu('tools', context()).groups;
		expect(groups.at(-1)?.map((item) => item.label)).toEqual(['Note']);
		dispose();
		expect(row('tools', 'Note', context())).toBeUndefined();
	});

	it('joins a rail group by id and sorts by order inside it', () => {
		const first = menuItems.register(entry({ id: 'acme.a', label: 'A', group: 'look', order: 20 }), pluginOwner('acme'));
		const second = menuItems.register(entry({ id: 'acme.b', label: 'B', group: 'look', order: 10 }), pluginOwner('acme'));
		const menu = buildMenu('tools', context());
		expect(menu.groups.map((group) => group.map((item) => item.label))).toEqual([['Crop', 'Resize'], ['Looks', 'B', 'A']]);
		first();
		second();
	});

	it('creates a top-level menu for an unknown menu id, labelled by the first entry', () => {
		const dispose = menuItems.register(entry({ id: 'acme.shelf', menu: 'acme.shelf', menuLabel: 'Shelf', group: 'main' }), pluginOwner('acme'));
		expect(menuTabs('image').map((tab) => tab.label)).toEqual(['File', 'Edit', 'View', 'Tools', 'Shelf']);
		expect(buildMenus(context()).map((menu) => menu.id)).toContain('acme.shelf');
		dispose();
		expect(menuTabs('image')).toHaveLength(4);
	});

	it('shows a reason from available and refuses to run', () => {
		const run = vi.fn();
		const dispose = menuItems.register(entry({ available: () => 'Not yet', run }), pluginOwner('acme'));
		const found = row('tools', 'Note', context())!;
		expect(found.reason).toBe('Not yet');
		found.run();
		expect(run).not.toHaveBeenCalled();
		dispose();
	});

	it('guards a throwing available, label and run and keeps the rest of the menu', () => {
		const boom = () => {
			throw new Error('boom');
		};
		const dispose = menuItems.register(entry({ available: boom, label: boom, run: boom, children: boom }), pluginOwner('acme'));
		const found = flatRows(buildMenu('tools', context())).find((item) => item.id === 'acme.note')!;
		expect(found.label).toBe('acme.note');
		expect(found.children).toEqual([]);
		expect(() => found.run()).not.toThrow();
		expect(hasFailed('image:acme.note')).toBe(true);
		expect(flatRows(buildMenu('tools', context())).some((item) => item.label === 'Crop')).toBe(true);
		dispose();
	});

	it('does not guard core entries, so a core error surfaces', () => {
		const dispose = menuItems.register(
			entry({
				id: 'core.boom',
				available: () => {
					throw new Error('core');
				}
			})
		);
		expect(() => buildMenu('tools', context())).toThrow('core');
		dispose();
	});

	it('refuses a plugin id that does not start with the plugin id', () => {
		const dispose = menuItems.register(entry({ id: 'other.note' }), pluginOwner('acme'));
		expect(row('tools', 'Note', context())).toBeUndefined();
		dispose();
	});

	it('hides an entry whose visible returns false', () => {
		const dispose = menuItems.register(entry({ visible: () => false }), pluginOwner('acme'));
		expect(row('tools', 'Note', context())).toBeUndefined();
		dispose();
	});
});

describe('menu items declared in a manifest', () => {
	const declared = {
		plugin_id: 'acme',
		id: 'acme.note',
		editor: 'image' as const,
		menu: 'tools',
		group: 'acme.group',
		order: 5,
		label: 'Note',
		icon: 'extension',
		keys: 'N',
		module: 'editor/note.js'
	};

	it('is busy until its module loads, then runs the module and keeps the manifest fields', async () => {
		let finish: (module: { available?: () => true | string; run?: () => void }) => void = () => undefined;
		const run = vi.fn();
		const loader = () => new Promise<{ available?: () => true | string; run?: () => void }>((resolve) => (finish = resolve));
		const { item, start } = declaredMenuItem(declared, loader as never);
		expect(item).toMatchObject({ id: 'acme.note', menu: 'tools', group: 'acme.group', order: 5, label: 'Note', keybind: 'N' });
		const stop = start();
		expect(item.available?.(context())).toBe('Loading');
		finish({ run, available: () => true });
		await Promise.resolve();
		await Promise.resolve();
		expect(item.available?.(context())).toBe(true);
		item.run?.(context());
		expect(run).toHaveBeenCalledTimes(1);
		stop();
	});

	it('is unavailable when its module cannot be loaded', async () => {
		const { item, start } = declaredMenuItem(declared, () => Promise.reject(new Error('404')));
		start();
		await Promise.resolve();
		await Promise.resolve();
		expect(item.available?.(context())).toBe('Could not load');
	});

	it('registers and removes through describeEditorEntries', () => {
		const snapshot = { ...emptyEditorSnapshot(), menu: [declared] };
		const descriptors = describeEditorEntries(snapshot, () => 'r1');
		expect(descriptors).toHaveLength(1);
		descriptors.forEach((descriptor) => descriptor.register());
		expect(menuItems.get('image', 'acme.note')?.pluginId).toBe('acme');
		descriptors.forEach((descriptor) => descriptor.dispose());
		expect(menuItems.get('image', 'acme.note')).toBeUndefined();
	});

	it('skips an entry without a plugin or id and refuses a foreign id', () => {
		const snapshot = { ...emptyEditorSnapshot(), menu: [{ ...declared, plugin_id: '' }, { ...declared, id: '' }, { ...declared, id: 'other.note' }] };
		const descriptors = describeEditorEntries(snapshot, () => 'r1');
		expect(descriptors).toHaveLength(1);
		descriptors.forEach((descriptor) => descriptor.register());
		expect(menuItems.get('image', 'other.note')).toBeUndefined();
		descriptors.forEach((descriptor) => descriptor.dispose());
	});
});
