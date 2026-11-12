import { afterEach, describe, it, expect, vi } from 'vitest';
import { sourceEntries, type MediaSource } from './mediaFieldSources';

function sources(kind: 'image' | 'video' | 'audio' | null, offersDraw = false): MediaSource[] {
	return sourceEntries({ kind, offersDraw }).map((entry) => entry.source);
}

describe('sourceEntries', () => {
	it('offers every door for an image', () => {
		expect(sources('image', true)).toEqual(['browse', 'paste', 'history', 'library', 'draw']);
	});

	it('leaves out paste and drawing for a video', () => {
		expect(sources('video', true)).toEqual(['browse', 'history', 'library']);
	});

	it('offers history for audio', () => {
		expect(sources('audio')).toEqual(['browse', 'history', 'library']);
	});

	it('offers drawing only where the host allows it', () => {
		expect(sources('image', false)).not.toContain('draw');
	});

	it('offers kits only where the host allows them, after the library', () => {
		const entries = sourceEntries({ kind: 'image', offersDraw: false, offersKits: true }).map((entry) => entry.source);
		expect(entries).toEqual(['browse', 'paste', 'history', 'library', 'kits']);
		expect(sources('image', false)).not.toContain('kits');
	});

	it('offers every door when the kind is not narrowed', () => {
		expect(sources(null, true)).toEqual(['browse', 'paste', 'history', 'library', 'draw']);
	});
});

describe('the paste shortcut hint', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	function pasteShortcut(): string | undefined {
		return sourceEntries({ kind: 'image', offersDraw: false }).find((entry) => entry.source === 'paste')?.shortcut;
	}

	it('names Ctrl on Windows and Linux', () => {
		vi.stubGlobal('navigator', { platform: 'Win32' });
		expect(pasteShortcut()).toBe('Ctrl V');
	});

	it('names the command key on macOS', () => {
		vi.stubGlobal('navigator', { platform: 'MacIntel' });
		expect(pasteShortcut()).toBe('⌘ V');
	});
});
