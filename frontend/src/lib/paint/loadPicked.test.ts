import { describe, expect, it } from 'vitest';
import { displayName, generationImageUrl, titleFromFilename } from './loadPicked';

describe('generationImageUrl', () => {
	it('builds the generation media route from a stored path', () => {
		expect(generationImageUrl('outputs/2025-10-10/01K77DF21Z/0.png')).toBe(
			'/api/media/generations/01K77DF21Z/0.png'
		);
	});

	it('refuses a path with no generation segment', () => {
		expect(generationImageUrl('0.png')).toBeNull();
		expect(generationImageUrl('')).toBeNull();
	});
});

describe('displayName', () => {
	it('prefers the first non-blank candidate', () => {
		expect(displayName(['  ', undefined, 'cat.png', 'dog.png'], 'image.png')).toBe('cat.png');
	});

	it('falls back when every candidate is blank', () => {
		expect(displayName([null, ''], 'image.png')).toBe('image.png');
	});
});

describe('titleFromFilename', () => {
	it('drops the extension and keeps the rest', () => {
		expect(titleFromFilename('dusk over the bay.png')).toBe('dusk over the bay');
		expect(titleFromFilename('a.b.jpg')).toBe('a.b');
	});

	it('has no title for a bare index, an empty name or nothing', () => {
		expect(titleFromFilename('0.png')).toBeNull();
		expect(titleFromFilename('  ')).toBeNull();
		expect(titleFromFilename(undefined)).toBeNull();
	});
});
