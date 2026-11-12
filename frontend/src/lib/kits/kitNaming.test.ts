import { describe, expect, it } from 'vitest';
import { checkName, copyName, defaultItemName, defaultItemNames, liveName, slugify, suggestFree } from './kitNaming';

describe('liveName', () => {
	it('lowercases and turns spaces into underscores as the user types', () => {
		expect(liveName('Profile Left')).toBe('profile_left');
		expect(liveName('a  b')).toBe('a_b');
	});
});

describe('slugify', () => {
	it('derives a path name from a display name', () => {
		expect(slugify('Old harbour')).toBe('old_harbour');
		expect(slugify('  Reading nook!  ')).toBe('reading_nook');
	});

	it('drops accents', () => {
		expect(slugify('Café Noir')).toBe('cafe_noir');
	});

	it('prefixes a name that does not start with a letter, as the server does', () => {
		expect(slugify('3D scans')).toBe('kit_3d_scans');
		expect(slugify('123 !!!')).toBe('kit_123');
	});

	it('caps the length without leaving a trailing underscore', () => {
		const slug = slugify('word '.repeat(20));
		expect(slug.length).toBeLessThanOrEqual(32);
		expect(slug.endsWith('_')).toBe(false);
	});

	it('falls back when nothing usable is left', () => {
		expect(slugify('!!!')).toBe('kit');
	});
});

describe('checkName', () => {
	it('accepts a valid name', () => {
		expect(checkName('face', ['hat']).ok).toBe(true);
	});

	it('refuses an empty name', () => {
		expect(checkName('').problem).toBe('empty');
	});

	it('refuses a name that starts with a digit or underscore', () => {
		expect(checkName('1face').problem).toBe('start');
		expect(checkName('_face').problem).toBe('start');
	});

	it('refuses capitals, spaces and symbols', () => {
		expect(checkName('Face').problem).toBe('chars');
		expect(checkName('fa ce').problem).toBe('chars');
		expect(checkName('fa-ce').problem).toBe('chars');
	});

	it('refuses a name longer than 32 characters', () => {
		expect(checkName('a'.repeat(33)).problem).toBe('length');
		expect(checkName('a'.repeat(32)).ok).toBe(true);
	});

	it('says already used and suggests the next free one', () => {
		const result = checkName('face', ['face', 'face2']);
		expect(result.problem).toBe('taken');
		expect(result.message).toBe('Already used.');
		expect(result.suggestion).toBe('face3');
	});
});

describe('suggestFree', () => {
	it('numbers from 2', () => {
		expect(suggestFree('face', ['face'])).toBe('face2');
	});

	it('replaces a trailing number rather than stacking', () => {
		expect(suggestFree('face2', ['face2', 'face3'])).toBe('face4');
	});
});

describe('default item names', () => {
	it('counts per kind and skips names in use', () => {
		expect(defaultItemName('image', [])).toBe('image1');
		expect(defaultItemName('image', ['image1', 'image3'])).toBe('image2');
		expect(defaultItemName('audio', ['image1'])).toBe('audio1');
	});

	it('names a batch in order without clashing with itself', () => {
		expect(defaultItemNames(['image', 'image', 'video', 'audio', 'text'])).toEqual([
			'image1',
			'image2',
			'video1',
			'audio1',
			'text1'
		]);
	});
});

describe('copyName', () => {
	it('appends copy', () => {
		expect(copyName('Mara')).toBe('Mara copy');
	});
});
