import { afterEach, describe, it, expect, vi } from 'vitest';
import { currentSourceOf, sourceEntries, type MediaSource, type SourceIntent } from './mediaFieldSources';

function sources(kind: 'image' | 'video' | 'audio' | null, offersDraw = false, intent?: SourceIntent): MediaSource[] {
	return sourceEntries({ kind, offersDraw, intent }).map((entry) => entry.source);
}

describe('sourceEntries', () => {
	it('offers every door for an image', () => {
		expect(sources('image', true)).toEqual(['browse', 'history', 'library', 'paste', 'draw']);
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
		expect(entries).toEqual(['browse', 'history', 'library', 'kits', 'paste']);
		expect(sources('image', false)).not.toContain('kits');
	});

	it('offers every door when the kind is not narrowed', () => {
		expect(sources(null, true)).toEqual(['browse', 'history', 'library', 'paste', 'draw']);
	});
});

describe('named entries', () => {
	it('names every source by what it is', () => {
		const labels = sourceEntries({ kind: 'image', offersDraw: true, offersKits: true }).map((entry) => entry.label);
		expect(labels).toEqual(['Upload file', 'History', 'Library', 'Kits', 'Paste', 'Draw']);
	});

	it('puts a divider before the clipboard and drawing extras only', () => {
		const entries = sourceEntries({ kind: 'image', offersDraw: true, offersKits: true });
		expect(entries.filter((entry) => entry.dividerBefore).map((entry) => entry.source)).toEqual(['paste']);
	});

	it('marks a kit as one item when an item is being replaced', () => {
		const change = sourceEntries({ kind: 'image', offersDraw: false, offersKits: true, intent: 'change' });
		expect(change.find((entry) => entry.source === 'kits')?.hint).toBe('one item');
		const add = sourceEntries({ kind: 'image', offersDraw: false, offersKits: true, intent: 'add' });
		expect(add.find((entry) => entry.source === 'kits')?.hint).toBeUndefined();
	});

	it('offers the same sources for a change as for an add', () => {
		expect(sources('image', true, 'change')).toEqual(sources('image', true, 'add'));
	});
});

describe('currentSourceOf', () => {
	it('reads a generation file as History', () => {
		expect(currentSourceOf({ url: '/api/media/generations/g1/a.png', relative_path: 'generations/g1/a.png' })).toBe('history');
	});

	it('reads an upload as Library', () => {
		expect(currentSourceOf({ url: '/api/media/uploads/a.png', relative_path: 'uploads/a.png' })).toBe('library');
		expect(currentSourceOf({ url: '/x', path: 'uploads/a.png' })).toBe('library');
	});

	it('reads anything else as no source', () => {
		expect(currentSourceOf({ url: '/api/media/serve?path=elsewhere/a.png' })).toBeNull();
		expect(currentSourceOf(null)).toBeNull();
		expect(currentSourceOf('text')).toBeNull();
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
