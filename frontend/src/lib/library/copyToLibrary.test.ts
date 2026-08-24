import { describe, it, expect } from 'vitest';
import { collectCopyableFileIds, summarizeCopyOutcome } from './copyToLibrary';

describe('collectCopyableFileIds', () => {
	it('returns every final image/video/audio file as a string id', () => {
		const ids = collectCopyableFileIds([
			{ files: [{ id: 1, file_type: 'image', is_final: true }] },
			{ files: [{ id: 2, file_type: 'video', is_final: true }] },
			{ files: [{ id: 3, file_type: 'audio', is_final: true }] }
		]);
		expect(ids).toEqual(['1', '2', '3']);
	});

	it('skips intermediate files', () => {
		const ids = collectCopyableFileIds([
			{ files: [{ id: 1, file_type: 'image', is_final: false }, { id: 2, file_type: 'image', is_final: true }] }
		]);
		expect(ids).toEqual(['2']);
	});

	it('keeps files whose is_final is absent', () => {
		expect(collectCopyableFileIds([{ files: [{ id: 7, file_type: 'image' }] }])).toEqual(['7']);
	});

	it('skips media kinds the library does not hold', () => {
		const ids = collectCopyableFileIds([
			{ files: [{ id: 1, file_type: 'mesh', is_final: true }, { id: 2, file_type: 'image', is_final: true }] }
		]);
		expect(ids).toEqual(['2']);
	});

	it('matches the file type case-insensitively', () => {
		expect(collectCopyableFileIds([{ files: [{ id: 1, file_type: 'IMAGE' }] }])).toEqual(['1']);
	});

	it('de-duplicates a file that appears twice', () => {
		const ids = collectCopyableFileIds([
			{ files: [{ id: 5, file_type: 'image' }] },
			{ files: [{ id: 5, file_type: 'image' }] }
		]);
		expect(ids).toEqual(['5']);
	});

	it('tolerates a generation with no files', () => {
		expect(collectCopyableFileIds([{}, { files: [] }])).toEqual([]);
	});
});

describe('summarizeCopyOutcome', () => {
	it('says copied, never moved', () => {
		expect(summarizeCopyOutcome(2, 0)).toBe('Copied 2 files to Library');
		expect(summarizeCopyOutcome(2, 0)).not.toMatch(/moved/i);
	});

	it('uses the singular for one file', () => {
		expect(summarizeCopyOutcome(1, 0)).toBe('Copied 1 file to Library');
	});

	it('reports a partial failure rather than rounding up to success', () => {
		expect(summarizeCopyOutcome(2, 1)).toBe('Copied 2 files to Library, 1 failed');
	});

	it('reports a total failure', () => {
		expect(summarizeCopyOutcome(0, 3)).toBe('Failed to copy 3 files to Library');
	});

	it('handles an empty batch', () => {
		expect(summarizeCopyOutcome(0, 0)).toBe('Nothing to copy to Library');
	});
});
