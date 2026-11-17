import { beforeAll, describe, expect, it, vi } from 'vitest';
import { editorToolIcons } from '$lib/utils/iconGlyphs';
import { builtinPaintTools, canonicalTool, isPaintEntry, paintEntryId, paintGroupOf, paintIconFor, paintToolOfEntry } from './entries';
import { modalRailGroups } from './railGroups';
import { paintToolForKey } from './toolKeys';
import { hexToHsv, hsvToHex, normalizeHex } from './colourMath';
import { rememberColour, recentColours, resetRecentColours } from './recentColours';

const key = (k: string, extra: Partial<KeyboardEvent> = {}) => ({ key: k, defaultPrevented: false, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, composedPath: () => [], isComposing: false, target: null, ...extra }) as unknown as KeyboardEvent;

describe('paint entries', () => {
	it('maps tool ids to rail entry ids and back', () => {
		expect(paintEntryId('brush')).toBe('paint.brush');
		expect(isPaintEntry('paint.brush')).toBe(true);
		expect(isPaintEntry('paint')).toBe(false);
		expect(paintToolOfEntry('paint.lasso')).toBe('lasso');
		expect(canonicalTool('paint')).toBe('paint.brush');
		expect(canonicalTool('crop')).toBe('crop');
	});

	it('puts the seven editor tools in two groups', () => {
		const groups = builtinPaintTools().map((tool) => [tool.id, paintGroupOf(tool)]);
		expect(groups).toEqual([['brush', 'paint'], ['eraser', 'paint'], ['fill', 'paint'], ['pick', 'paint'], ['rect', 'select'], ['lasso', 'select'], ['move', 'select']]);
	});

	it('gives every tool its own icon from the shared map', () => {
		const names = builtinPaintTools().map((tool) => paintIconFor(tool));
		expect(names).toEqual(['brush', 'eraser', 'fill', 'pick', 'rect', 'lasso', 'move'].map((id) => editorToolIcons[id]));
		expect(new Set(names).size).toBe(names.length);
	});

	it('groups the tools of a session into the modal rail', () => {
		const tools = [...builtinPaintTools()];
		const groups = modalRailGroups(tools);
		expect(groups.map((group) => group.label)).toEqual(['Paint', 'Select and move']);
	});
});

describe('tool letter keys', () => {
	beforeAll(() => {
		for (const name of ['Element', 'HTMLInputElement', 'HTMLTextAreaElement', 'HTMLSelectElement']) vi.stubGlobal(name, class {});
	});

	const tools = builtinPaintTools();
	it('selects the tool for a plain letter', () => {
		expect(paintToolForKey(key('b'), tools)).toBe('brush');
		expect(paintToolForKey(key('V'), tools)).toBe('move');
		expect(paintToolForKey(key('l'), tools)).toBe('lasso');
	});

	it('ignores modifiers and unbound letters', () => {
		expect(paintToolForKey(key('b', { ctrlKey: true }), tools)).toBeNull();
		expect(paintToolForKey(key('b', { shiftKey: true }), tools)).toBeNull();
		expect(paintToolForKey(key('z'), tools)).toBeNull();
		expect(paintToolForKey(key('Enter'), tools)).toBeNull();
	});
});

describe('colour maths', () => {
	it('round trips hex through hsv', () => {
		for (const hex of ['#f5a524', '#111111', '#ffffff', '#3e63dd']) expect(hsvToHex(hexToHsv(hex))).toBe(hex);
	});

	it('normalizes short and long hex', () => {
		expect(normalizeHex('#ABC')).toBe('#aabbcc');
		expect(normalizeHex('f5a524')).toBe('#f5a524');
		expect(normalizeHex('zz')).toBeNull();
	});
});

describe('recent colours', () => {
	it('keeps the newest first without repeats', () => {
		resetRecentColours();
		rememberColour('#111111');
		rememberColour('#222222');
		rememberColour('#111111');
		expect(recentColours().slice(0, 2)).toEqual(['#111111', '#222222']);
	});
});
