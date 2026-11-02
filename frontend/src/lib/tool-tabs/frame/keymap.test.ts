import { describe, expect, it } from 'vitest';
import { resolveFrameKey, shortcutFor, type FrameKey } from './keymap';

function key(k: string, mods: Partial<FrameKey> = {}): FrameKey {
	return { key: k, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...mods };
}

describe('frame keymap', () => {
	it('maps Ctrl Z, Ctrl Shift Z and Ctrl S, with Cmd on a Mac', () => {
		expect(resolveFrameKey(key('z', { ctrlKey: true }))).toBe('undo');
		expect(resolveFrameKey(key('Z', { ctrlKey: true, shiftKey: true }))).toBe('redo');
		expect(resolveFrameKey(key('z', { metaKey: true }))).toBe('undo');
		expect(resolveFrameKey(key('s', { ctrlKey: true }))).toBe('save');
		expect(resolveFrameKey(key('s', { metaKey: true }))).toBe('save');
	});

	it('maps backslash, Enter and Space without modifiers', () => {
		expect(resolveFrameKey(key('\\'))).toBe('compare');
		expect(resolveFrameKey(key('Enter'))).toBe('apply');
		expect(resolveFrameKey(key(' '))).toBe('pan');
	});

	it('ignores auto-repeat for the toggles but keeps Space held', () => {
		expect(resolveFrameKey(key('\\', { repeat: true }))).toBeNull();
		expect(resolveFrameKey(key('Enter', { repeat: true }))).toBeNull();
		expect(resolveFrameKey(key(' ', { repeat: true }))).toBe('pan');
	});

	it('leaves the generate-page shortcuts alone', () => {
		for (const letter of ['g', 't', 'x', 'f', 'h', 's', 'w', 'e', 'q', 'c', 'b', 'a', '1', '?']) {
			expect(resolveFrameKey(key(letter)), letter).toBeNull();
		}
		expect(resolveFrameKey(key('Enter', { ctrlKey: true }))).toBeNull();
		expect(resolveFrameKey(key('t', { ctrlKey: true }))).toBeNull();
	});

	it('ignores Alt chords, Shift on plain keys and Ctrl Shift S', () => {
		expect(resolveFrameKey(key('z', { ctrlKey: true, altKey: true }))).toBeNull();
		expect(resolveFrameKey(key('Enter', { shiftKey: true }))).toBeNull();
		expect(resolveFrameKey(key('s', { ctrlKey: true, shiftKey: true }))).toBeNull();
		expect(resolveFrameKey(key('y', { ctrlKey: true }))).toBeNull();
	});

	it('names the keys shown in tooltips and menus', () => {
		expect(shortcutFor('undo')).toBe('Ctrl Z');
		expect(shortcutFor('redo')).toBe('Ctrl Shift Z');
		expect(shortcutFor('save')).toBe('Ctrl S');
		expect(shortcutFor('compare')).toBe('\\');
	});
});
