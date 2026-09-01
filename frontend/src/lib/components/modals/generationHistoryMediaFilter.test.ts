import { describe, expect, it } from 'vitest';
import { filterFilesByMediaType } from './generationHistoryMediaFilter';

const IMAGE = { id: 'i1', file_type: 'IMAGE' };
const VIDEO = { id: 'v1', file_type: 'video' };
const AUDIO = { id: 'a1', file_type: 'AUDIO' };
const MESH = { id: 'm1', file_type: 'MESH' };

describe('filterFilesByMediaType', () => {
	it('passes everything through when mediaType is unset', () => {
		expect(filterFilesByMediaType([IMAGE, VIDEO, AUDIO, MESH], undefined)).toEqual([IMAGE, VIDEO, AUDIO, MESH]);
	});

	it('keeps only image files for image', () => {
		expect(filterFilesByMediaType([IMAGE, VIDEO, AUDIO, MESH], 'image')).toEqual([IMAGE]);
	});

	it('keeps only video files for video', () => {
		expect(filterFilesByMediaType([IMAGE, VIDEO, AUDIO, MESH], 'video')).toEqual([VIDEO]);
	});

	it('keeps only audio files for audio', () => {
		expect(filterFilesByMediaType([IMAGE, VIDEO, AUDIO, MESH], 'audio')).toEqual([AUDIO]);
	});

	it('keeps only mesh files for mesh', () => {
		expect(filterFilesByMediaType([IMAGE, VIDEO, AUDIO, MESH], 'mesh')).toEqual([MESH]);
	});

	it('drops every file for an unrecognized media type instead of bucketing it as video', () => {
		// A caller smuggling an unknown type through a stale type cast used to
		// fall into the `: isVideo` branch and surface video files under a
		// bogus selection. Assert the closed switch instead.
		const filtered = filterFilesByMediaType([IMAGE, VIDEO, AUDIO, MESH], 'bogus' as any);
		expect(filtered).toEqual([]);
	});

	it('handles a missing files array', () => {
		expect(filterFilesByMediaType(undefined, 'image')).toEqual([]);
		expect(filterFilesByMediaType(null, 'image')).toEqual([]);
	});
});
