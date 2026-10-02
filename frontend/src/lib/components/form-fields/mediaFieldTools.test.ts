import { describe, it, expect } from 'vitest';
import { registerMediaTool, unregisterMediaTools, type MediaTool } from '$lib/tools/tools';
import { buildMediaGroups } from './mediaFieldGroups';
import {
	buildFieldToolGroups,
	fieldToolGroupsFor,
	fieldToolIds,
	matchToolShortcut,
	type FieldToolEnv,
	type FieldToolOptions
} from './mediaFieldTools';

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
		...overrides
	};
}

function availability(opts: FieldToolOptions, id: string) {
	for (const group of buildFieldToolGroups(opts)) {
		for (const entry of group.tools) if (entry.tool.id === id) return entry.availability;
	}
	return undefined;
}

describe('tools per kind', () => {
	it('offers edit and crop for an image, and no mask without allow_inpaint', () => {
		expect(fieldToolIds(options())).toEqual(['edit', 'crop', 'full']);
	});

	it('adds the mask tools for an image when the field allows inpainting', () => {
		expect(fieldToolIds(options({ allowInpaint: true }))).toEqual(['edit', 'crop', 'mask', 'clear-mask', 'full']);
	});

	it('offers trim and frame for a video and never the image tools', () => {
		expect(fieldToolIds(options({ kind: 'video', allowInpaint: true }))).toEqual(['trim', 'frame', 'full']);
	});

	it('offers trim and split for audio', () => {
		expect(fieldToolIds(options({ kind: 'audio' }))).toEqual(['trim', 'split', 'full']);
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
		for (const id of ['edit', 'crop', 'mask', 'full']) {
			expect(availability(opts, id)).toEqual({ enabled: false, reason: 'File not found' });
		}
	});
});

describe('multi field tools', () => {
	const multi = (overrides: Partial<FieldToolOptions> = {}) =>
		options({ multiple: true, groupSize: 3, kindIndex: 1, totalItems: 5, ...overrides });

	it('adds order and source groups only to a multi field', () => {
		expect(fieldToolIds(options())).not.toContain('replace');
		expect(fieldToolIds(multi())).toEqual(['edit', 'crop', 'full', 'earlier', 'later', 'replace', 'remove-all']);
	});

	it('greys Move earlier on the first item of its group and Move later on the last', () => {
		expect(availability(multi({ kindIndex: 0 }), 'earlier')).toEqual({ enabled: false, reason: 'First' });
		expect(availability(multi({ kindIndex: 0 }), 'later')).toEqual({ enabled: true });
		expect(availability(multi({ kindIndex: 2 }), 'later')).toEqual({ enabled: false, reason: 'Last' });
		expect(availability(multi({ kindIndex: 1 }), 'earlier')).toEqual({ enabled: true });
	});

	it('counts every item in the Remove all label', () => {
		const label = buildFieldToolGroups(multi())[3].tools[1].tool.label;
		expect(label).toBe('Remove all 5');
	});
});

describe('compact row tools', () => {
	it('puts replace and remove in the menu because the row has no room for buttons', () => {
		expect(fieldToolIds(options({ inRow: true }))).toEqual(['edit', 'crop', 'full', 'replace', 'remove']);
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
	});

	it('ignores a key bound to a tool that is greyed out or absent', () => {
		expect(matchToolShortcut(buildFieldToolGroups(options({ missing: true })), 'e')).toBeNull();
		expect(matchToolShortcut(buildFieldToolGroups(options()), 't')).toBeNull();
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
});
