import { describe, it, expect } from 'vitest';
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

	it('offers every door when the kind is not narrowed', () => {
		expect(sources(null, true)).toEqual(['browse', 'paste', 'history', 'library', 'draw']);
	});
});
