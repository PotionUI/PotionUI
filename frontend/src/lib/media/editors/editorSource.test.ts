import { describe, it, expect } from 'vitest';
import {
	generationLocatorFromPath,
	matchGenerationFileId,
	matchUploadId,
	uploadFilenameFromPath
} from './editorSource';

describe('uploadFilenameFromPath', () => {
	it('reads the relative path the field stores after an upload', () => {
		expect(uploadFilenameFromPath('uploads/8f3c-9a.png')).toBe('8f3c-9a.png');
	});

	it('reads an absolute path from an older stored value', () => {
		expect(uploadFilenameFromPath('/srv/storage/uploads/8f3c-9a.png')).toBe('8f3c-9a.png');
	});

	it('refuses a generation path - a generated file is not a library row', () => {
		expect(uploadFilenameFromPath('generations/2026-08-13/01KABC/0.png')).toBeNull();
	});

	it('refuses a temporary path', () => {
		expect(uploadFilenameFromPath('tmp/mask-1.png')).toBeNull();
	});

	it('refuses a bare filename with no uploads parent', () => {
		expect(uploadFilenameFromPath('8f3c-9a.png')).toBeNull();
	});

	it('refuses a path that only ends at the directory', () => {
		expect(uploadFilenameFromPath('uploads/')).toBeNull();
	});

	it('answers null for the empty and missing cases', () => {
		expect(uploadFilenameFromPath('')).toBeNull();
		expect(uploadFilenameFromPath(null)).toBeNull();
		expect(uploadFilenameFromPath(undefined)).toBeNull();
	});
});

describe('generationLocatorFromPath', () => {
	it('takes the generation id from the second-to-last segment', () => {
		expect(generationLocatorFromPath('outputs/2026-08-13/01K77DF21Z/0.png')).toEqual({
			generationId: '01K77DF21Z',
			filename: '0.png'
		});
	});

	it('refuses an upload path, which is already a row', () => {
		expect(generationLocatorFromPath('uploads/8f3c-9a.png')).toBeNull();
	});

	it('refuses a temporary path, which is not persisted media', () => {
		expect(generationLocatorFromPath('tmp/mask-1.png')).toBeNull();
		expect(generationLocatorFromPath('/var/data/tmp/mask-1.png')).toBeNull();
	});

	it('answers null for the empty and missing cases', () => {
		expect(generationLocatorFromPath('')).toBeNull();
		expect(generationLocatorFromPath('0.png')).toBeNull();
		expect(generationLocatorFromPath(null)).toBeNull();
	});
});

describe('matchUploadId', () => {
	const uploads = [
		{ id: 'row-1', filename: 'aaa.png' },
		{ id: 'row-2', filename: 'bbb.mp4' }
	];

	it('matches on the stored filename, never on the display name', () => {
		expect(matchUploadId(uploads, 'bbb.mp4')).toBe('row-2');
	});

	it('answers null when the page holds no match', () => {
		expect(matchUploadId(uploads, 'ccc.wav')).toBeNull();
		expect(matchUploadId([], 'aaa.png')).toBeNull();
	});
});

describe('matchGenerationFileId', () => {
	it('finds the file the value names inside its generation', () => {
		const media = [
			{ id: 'file-a', filename: '0.png' },
			{ id: 'file-b', filename: '1.png' }
		];
		expect(matchGenerationFileId(media, '1.png')).toBe('file-b');
		expect(matchGenerationFileId(media, '2.png')).toBeNull();
	});
});
