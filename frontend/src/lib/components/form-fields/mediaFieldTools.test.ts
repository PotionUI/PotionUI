import { describe, it, expect } from 'vitest';
import { registerMediaTool, unregisterMediaTools, type MediaTool } from '$lib/tools/tools';
import type { MediaToolSource, ToolDTO } from '$lib/media-tools/types';
import { buildMediaGroups } from './mediaFieldGroups';
import {
	buildFieldToolGroups,
	fieldCatalogTools,
	fieldToolEditorKind,
	fieldToolGroupsFor,
	FIELD_TOOL_EDITORS,
	matchToolShortcut,
	type FieldToolEnv,
	type FieldToolOptions
} from './mediaFieldTools';

function dto(overrides: Partial<ToolDTO> & { id: string }): ToolDTO {
	return {
		label: overrides.id,
		icon: 'sliders',
		accepts: ['image'],
		produces: 'same',
		cardinality: 'one_to_one',
		params: [],
		requires: {},
		selection: null,
		scopes: ['library', 'history', 'field'],
		editor: null,
		preview: false,
		component: null,
		runnable: true,
		source: 'core',
		last_params: null,
		...overrides
	};
}

const CATALOG: ToolDTO[] = [
	dto({ id: 'trim', label: 'Trim', accepts: ['video', 'audio'], editor: 'trim' }),
	dto({ id: 'crop', label: 'Crop', accepts: ['image', 'video'], editor: 'crop' }),
	dto({ id: 'resize', label: 'Resize', accepts: ['image', 'video'], editor: 'resize' }),
	dto({ id: 'rotate', label: 'Rotate', accepts: ['image', 'video'], preview: true }),
	dto({ id: 'flip', label: 'Flip', accepts: ['image', 'video'], preview: true }),
	dto({ id: 'speed', label: 'Speed', accepts: ['video', 'audio'] }),
	dto({ id: 'fit_canvas', label: 'Fit canvas', accepts: ['image'], preview: true }),
	dto({ id: 'crop_subject', label: 'Crop to subject', accepts: ['image'], requires: { has_alpha: true } }),
	dto({ id: 'frame', label: 'Frame', accepts: ['video'], produces: 'image', editor: 'frame' }),
	dto({ id: 'split', label: 'Split', accepts: ['audio'], cardinality: 'one_to_many', editor: 'split' }),
	dto({ id: 'concat', label: 'Concat', accepts: ['video'], cardinality: 'many_to_one', selection: { min: 2, max: 8 } }),
	dto({ id: 'history-only', label: 'History only', accepts: ['image'], scopes: ['history'] }),
	dto({
		id: 'acme.viewer',
		label: 'Viewer',
		accepts: ['image'],
		component: 'plugin:acme:viewer',
		runnable: false,
		source: 'acme'
	})
];

function options(overrides: Partial<FieldToolOptions> = {}): FieldToolOptions {
	return {
		kind: 'image',
		multiple: false,
		allowInpaint: false,
		canEmitMask: true,
		hasMask: false,
		missing: false,
		groupSize: 1,
		kindIndex: 0,
		totalItems: 1,
		catalogTools: fieldCatalogTools(CATALOG, overrides.kind ?? 'image'),
		...overrides
	};
}

function fieldToolIds(opts: FieldToolOptions) {
	return buildFieldToolGroups(opts).flatMap((group) => group.tools.map((entry) => entry.tool.id));
}

function availability(opts: FieldToolOptions, id: string) {
	for (const group of buildFieldToolGroups(opts)) {
		for (const entry of group.tools) if (entry.tool.id === id) return entry.availability;
	}
	return undefined;
}

describe('tools per kind', () => {
	it('offers the edit image tool and every catalog tool for an image, and no mask without allow_inpaint', () => {
		expect(fieldToolIds(options())).toEqual([
			'edit',
			'open-in-editor',
			'crop',
			'resize',
			'rotate',
			'flip',
			'fit_canvas',
			'crop_subject',
			'acme.viewer',
			'full'
		]);
	});

	it('puts the mask tools after the catalog tools when the field allows inpainting', () => {
		expect(fieldToolIds(options({ allowInpaint: true }))).toEqual([
			'edit',
			'open-in-editor',
			'crop',
			'resize',
			'rotate',
			'flip',
			'fit_canvas',
			'crop_subject',
			'acme.viewer',
			'mask',
			'clear-mask',
			'full'
		]);
	});

	it('offers the video tools of the catalog for a video and never the image-only ones', () => {
		expect(fieldToolIds(options({ kind: 'video', allowInpaint: true }))).toEqual([
			'open-in-editor',
			'trim',
			'crop',
			'resize',
			'rotate',
			'flip',
			'speed',
			'frame',
			'full'
		]);
	});

	it('offers trim, speed and split for audio', () => {
		expect(fieldToolIds(options({ kind: 'audio' }))).toEqual(['trim', 'speed', 'split', 'full']);
	});

	it('leaves out tools that are not offered in a field, and a join, which needs several items', () => {
		const ids = fieldCatalogTools(CATALOG, 'video').map((tool) => tool.id);
		expect(ids).not.toContain('concat');
		expect(fieldCatalogTools(CATALOG, 'image').map((tool) => tool.id)).not.toContain('history-only');
	});

	it('offers only the edit image and open in editor tools when the catalog has not loaded', () => {
		expect(fieldToolIds(options({ catalogTools: [] }))).toEqual(['edit', 'open-in-editor', 'full']);
	});

	it('takes the label and icon from the catalog', () => {
		const rotate = buildFieldToolGroups(options())[0].tools.find((entry) => entry.tool.id === 'rotate');
		expect(rotate?.tool.label).toBe('Rotate');
		expect(rotate?.tool.scopes).toEqual(['field']);
	});

	it('hands a component-only tool to its component rather than a dialog', () => {
		const viewer = buildFieldToolGroups(options())[0].tools.find((entry) => entry.tool.id === 'acme.viewer');
		expect(viewer?.tool.componentRef).toBe('plugin:acme:viewer');
		expect(viewer?.tool.source).toBe('plugin');
		const rotate = buildFieldToolGroups(options())[0].tools.find((entry) => entry.tool.id === 'rotate');
		expect(rotate?.tool.componentRef).toBeUndefined();
	});
});

describe('which surface a field tool opens', () => {
	it('opens the built-in editor a tool names', () => {
		expect(fieldToolEditorKind('crop', CATALOG)).toBe('crop');
		expect(fieldToolEditorKind('trim', CATALOG)).toBe('trim');
		expect(fieldToolEditorKind('frame', CATALOG)).toBe('frame');
		expect(fieldToolEditorKind('split', CATALOG)).toBe('split');
		expect(fieldToolEditorKind('resize', CATALOG)).toBe('resize');
	});

	it('opens the painter and the mask editor for the two tools that are not in the catalog', () => {
		expect(fieldToolEditorKind('edit', CATALOG)).toBe('paint');
		expect(fieldToolEditorKind('mask', CATALOG)).toBe('mask');
	});

	it('opens the dialog for a tool that has no editor', () => {
		expect(fieldToolEditorKind('rotate', CATALOG)).toBe('tool:rotate');
		expect(fieldToolEditorKind('speed', CATALOG)).toBe('tool:speed');
	});

	it('opens nothing for a component-only tool, an unknown tool or a menu action', () => {
		expect(fieldToolEditorKind('acme.viewer', CATALOG)).toBeNull();
		expect(fieldToolEditorKind('nope', CATALOG)).toBeNull();
		expect(fieldToolEditorKind('full', CATALOG)).toBeNull();
		expect(fieldToolEditorKind('remove', CATALOG)).toBeNull();
	});

	it('answers the lookup the field makes by tool id from the loaded catalog', () => {
		expect(FIELD_TOOL_EDITORS.edit).toBe('paint');
		expect(FIELD_TOOL_EDITORS.mask).toBe('mask');
		expect(FIELD_TOOL_EDITORS.full).toBeUndefined();
	});

	it('offers nothing for an item of unknown kind', () => {
		expect(buildFieldToolGroups(options({ kind: null }))).toEqual([]);
	});
});

describe('tool availability', () => {
	it('names the mask as create or edit by whether one is held', () => {
		const create = buildFieldToolGroups(options({ allowInpaint: true }))[0].tools.find((t) => t.tool.id === 'mask');
		const edit = buildFieldToolGroups(options({ allowInpaint: true, hasMask: true }))[0].tools.find(
			(t) => t.tool.id === 'mask'
		);
		expect(create?.tool.label).toBe('Create inpainting mask');
		expect(edit?.tool.label).toBe('Edit inpainting mask');
	});

	it('greys Clear mask with a reason until a mask exists', () => {
		expect(availability(options({ allowInpaint: true }), 'clear-mask')).toEqual({ enabled: false, reason: 'No mask' });
		expect(availability(options({ allowInpaint: true, hasMask: true }), 'clear-mask')).toEqual({ enabled: true });
	});

	it('greys the mask tool when the host cannot receive a mask', () => {
		expect(availability(options({ allowInpaint: true, canEmitMask: false }), 'mask')).toEqual({
			enabled: false,
			reason: 'Not available in this form'
		});
	});

	it('greys every tool that needs the file when it is missing', () => {
		const opts = options({ allowInpaint: true, missing: true });
		for (const id of ['edit', 'crop', 'rotate', 'mask', 'full']) {
			expect(availability(opts, id)).toEqual({ enabled: false, reason: 'File not found' });
		}
	});

	it('greys Crop to subject with a reason for a file that cannot be transparent', () => {
		const jpeg: MediaToolSource = { id: 'field', kind: 'image', url: '/a.jpg', filename: 'a.jpg' };
		expect(availability(options({ source: jpeg }), 'crop_subject')).toEqual({
			enabled: false,
			reason: 'Needs a transparent image'
		});
		const png: MediaToolSource = { id: 'field', kind: 'image', url: '/a.png', filename: 'a.png' };
		expect(availability(options({ source: png }), 'crop_subject')).toEqual({ enabled: true });
	});
});

describe('multi field tools', () => {
	const multi = (overrides: Partial<FieldToolOptions> = {}) =>
		options({ multiple: true, groupSize: 3, kindIndex: 1, totalItems: 5, ...overrides });

	it('adds order and source groups only to a multi field', () => {
		expect(fieldToolIds(options())).not.toContain('replace');
		expect(fieldToolIds(multi()).slice(-5)).toEqual(['full', 'earlier', 'later', 'replace', 'remove-all']);
	});

	it('greys Move earlier on the first item of its group and Move later on the last', () => {
		expect(availability(multi({ kindIndex: 0 }), 'earlier')).toEqual({ enabled: false, reason: 'First' });
		expect(availability(multi({ kindIndex: 0 }), 'later')).toEqual({ enabled: true });
		expect(availability(multi({ kindIndex: 2 }), 'later')).toEqual({ enabled: false, reason: 'Last' });
		expect(availability(multi({ kindIndex: 1 }), 'earlier')).toEqual({ enabled: true });
	});

	it('counts every item in the Remove all label', () => {
		const groups = buildFieldToolGroups(multi());
		const label = groups[groups.length - 1].tools[1].tool.label;
		expect(label).toBe('Remove all 5');
	});
});

describe('compact row tools', () => {
	it('puts replace and remove in the menu because the row has no room for buttons', () => {
		expect(fieldToolIds(options({ inRow: true })).slice(-3)).toEqual(['full', 'replace', 'remove']);
	});

	it('keeps remove-all after remove in a multi row', () => {
		const ids = fieldToolIds(options({ inRow: true, multiple: true, groupSize: 2, kindIndex: 0, totalItems: 2 }));
		expect(ids.slice(-3)).toEqual(['replace', 'remove', 'remove-all']);
	});
});

describe('tool shortcuts', () => {
	it('finds the tool a key is bound to, in either case', () => {
		const groups = buildFieldToolGroups(options({ allowInpaint: true }));
		expect(matchToolShortcut(groups, 'e')?.id).toBe('edit');
		expect(matchToolShortcut(groups, 'C')?.id).toBe('crop');
		expect(matchToolShortcut(groups, 'm')?.id).toBe('mask');
		expect(matchToolShortcut(buildFieldToolGroups(options({ kind: 'video' })), 't')?.id).toBe('trim');
		expect(matchToolShortcut(buildFieldToolGroups(options({ kind: 'video' })), 'f')?.id).toBe('frame');
	});

	it('ignores a key bound to a tool that is greyed out or absent', () => {
		expect(matchToolShortcut(buildFieldToolGroups(options({ missing: true })), 'e')).toBeNull();
		expect(matchToolShortcut(buildFieldToolGroups(options()), 't')).toBeNull();
		expect(matchToolShortcut(buildFieldToolGroups(options({ source: { id: 'field', kind: 'image', url: '/a.png', filename: 'a.png' }, missing: true })), 'c')).toBeNull();
	});

	it('ignores multi-character keys', () => {
		expect(matchToolShortcut(buildFieldToolGroups(options()), 'Enter')).toBeNull();
	});
});

describe('fieldToolGroupsFor', () => {
	const items = [
		{ url: '/a.png', name: 'a.png', type: 'image' },
		{ url: '/b.png', name: 'b.png', type: 'image' }
	];
	const groups = buildMediaGroups(items, ['image'], { maxItems: null, maxItemsByKind: {} }, () => 'image');

	function env(overrides: Partial<FieldToolEnv> = {}): FieldToolEnv {
		return {
			multiple: true,
			groups,
			allowInpaint: false,
			canEmitMask: true,
			hasMask: false,
			totalItems: 2,
			registrations: 0,
			catalog: CATALOG,
			...overrides
		};
	}

	const target = { kind: 'image' as const, item: items[1], flatIndex: 1, inRow: false, missing: false };

	function plugin(scopes: MediaTool['scopes']): MediaTool {
		return {
			id: 'acme:upscale',
			label: 'Upscale',
			icon: 'extension',
			category: 'compose',
			source: 'plugin',
			scopes,
			applies: () => ({ enabled: true })
		};
	}

	it('works out where the item sits in its group from its flat index', () => {
		const groupsOut = fieldToolGroupsFor(env(), target);
		const order = groupsOut.find((group) => group.category.id === 'order');
		expect(order?.tools.map((entry) => [entry.tool.id, entry.availability.enabled])).toEqual([
			['earlier', true],
			['later', false]
		]);
	});

	it('appends the tools plugins offer to a field after the core groups', () => {
		registerMediaTool(plugin(['field']));
		try {
			const ids = fieldToolGroupsFor(env(), target).flatMap((group) => group.tools.map((entry) => entry.tool.id));
			expect(ids.at(-1)).toBe('acme:upscale');
			expect(ids).toContain('edit');
		} finally {
			unregisterMediaTools((tool) => tool.id === 'acme:upscale');
		}
	});

	it('leaves out a plugin tool that is not scoped to a field', () => {
		registerMediaTool(plugin(['history']));
		try {
			const ids = fieldToolGroupsFor(env(), target).flatMap((group) => group.tools.map((entry) => entry.tool.id));
			expect(ids).not.toContain('acme:upscale');
		} finally {
			unregisterMediaTools((tool) => tool.id === 'acme:upscale');
		}
	});

	it('offers nothing for an item whose kind is unknown', () => {
		expect(fieldToolGroupsFor(env(), { ...target, kind: null })).toEqual([]);
	});

	it('offers nothing while an upload has not produced an item yet', () => {
		expect(fieldToolGroupsFor(env(), { ...target, item: null })).toEqual([]);
	});
});
