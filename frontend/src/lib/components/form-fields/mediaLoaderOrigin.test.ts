import { describe, it, expect } from 'vitest';
import { originFileIndex } from './mediaLoaderOrigin';

const files = [
	{ id: 11, file_path: 'generations/2026-01-01/gen/tmp.png' },
	{ id: 12, file_path: 'generations/2026-01-01/gen/1.png' },
	{ id: 13, file_path: 'generations/2026-01-01/gen/2.png' }
];

describe('originFileIndex', () => {
	it('locates the file by id against the unfiltered files array', () => {
		expect(originFileIndex({ files }, files[2])).toBe(2);
	});

	it('falls back to file_path when the record carries no id', () => {
		expect(
			originFileIndex({ files }, { file_path: 'generations/2026-01-01/gen/1.png' })
		).toBe(1);
	});

	it('returns -1 rather than 0 for a file the generation does not own', () => {
		// 0 would look like valid provenance and attribute another file's params.
		expect(originFileIndex({ files }, { id: 99, file_path: 'elsewhere/9.png' })).toBe(-1);
		expect(originFileIndex({ files }, null)).toBe(-1);
		expect(originFileIndex(null, files[0])).toBe(-1);
	});
});
