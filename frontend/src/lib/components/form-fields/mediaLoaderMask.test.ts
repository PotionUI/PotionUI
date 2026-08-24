import { describe, it, expect } from 'vitest';
import { maskSubjectKey, shouldClearMask } from './mediaLoaderMask';

const uploaded = {
	path: '/abs/uploads/cat.png',
	relative_path: 'uploads/cat.png',
	url: 'blob:http://localhost/1111',
	name: 'cat.png',
	type: 'image'
};

const fromGeneration = {
	path: 'generations/2026-08-13/gen-1/0.png',
	relative_path: 'generations/2026-08-13/gen-1/0.png',
	url: '/api/media/generations/gen-1/0.png',
	name: '0.png',
	type: 'image'
};

describe('maskSubjectKey', () => {
	it('identifies a media item by its stored relative path', () => {
		expect(maskSubjectKey(uploaded)).toBe('uploads/cat.png');
		expect(maskSubjectKey(fromGeneration)).toBe('generations/2026-08-13/gen-1/0.png');
	});

	it('accepts the legacy string value shape', () => {
		expect(maskSubjectKey('generations/2026-08-13/gen-1/0.png')).toBe(
			'generations/2026-08-13/gen-1/0.png'
		);
	});

	it('falls back to path, then url, when there is no relative_path', () => {
		expect(maskSubjectKey({ path: '/abs/uploads/x.png' })).toBe('/abs/uploads/x.png');
		expect(maskSubjectKey({ url: '/api/media/uploads/x.png' })).toBe('/api/media/uploads/x.png');
	});

	it('is null for an empty or unusable value', () => {
		expect(maskSubjectKey(null)).toBeNull();
		expect(maskSubjectKey(undefined)).toBeNull();
		expect(maskSubjectKey('')).toBeNull();
		expect(maskSubjectKey({})).toBeNull();
		expect(maskSubjectKey({ name: 'cat.png' })).toBeNull();
	});
});

describe('shouldClearMask', () => {
	// The bug: a mask painted on image A stayed attached after B replaced it,
	// so B was generated through A's shape.
	it('clears when the image is replaced by a different one', () => {
		const subject = maskSubjectKey(uploaded);
		expect(shouldClearMask(subject, fromGeneration)).toBe(true);
	});

	it('clears when the image is removed entirely', () => {
		expect(shouldClearMask(maskSubjectKey(uploaded), null)).toBe(true);
	});

	it('keeps the mask while the same image is still selected', () => {
		const subject = maskSubjectKey(uploaded);
		expect(shouldClearMask(subject, uploaded)).toBe(false);
	});

	// A re-render rebuilds the value object and mints a fresh blob url; identity
	// has to survive that or the mask would be dropped the instant it is painted.
	it('keeps the mask when only the object identity and blob url change', () => {
		const subject = maskSubjectKey(uploaded);
		const rerendered = { ...uploaded, url: 'blob:http://localhost/2222' };
		expect(shouldClearMask(subject, rerendered)).toBe(false);
	});

	it('does nothing when no mask is held', () => {
		expect(shouldClearMask(null, uploaded)).toBe(false);
		expect(shouldClearMask(null, null)).toBe(false);
	});

	it('clears when a same-named file is re-uploaded to a different path', () => {
		const subject = maskSubjectKey(uploaded);
		expect(shouldClearMask(subject, { ...uploaded, relative_path: 'uploads/cat_1.png' })).toBe(true);
	});
});
