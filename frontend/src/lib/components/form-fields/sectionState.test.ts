import { describe, expect, it } from 'vitest';
import { buildSectionStorageKey, foldedForScope, resolveSectionCollapsed } from './sectionState';

describe('buildSectionStorageKey', () => {
	it('joins preset/mode/path with slashes', () => {
		expect(buildSectionStorageKey('sdxl-anime', 'txt2img', 'advanced')).toBe(
			'sdxl-anime/txt2img/advanced'
		);
	});

	it('is stable for a nested path segment already containing slashes', () => {
		expect(buildSectionStorageKey('sdxl-anime', 'txt2img', 'tabs/refiner/advanced')).toBe(
			'sdxl-anime/txt2img/tabs/refiner/advanced'
		);
	});

	it.each([
		['missing preset', null, 'txt2img', 'advanced'],
		['missing mode', 'sdxl-anime', null, 'advanced'],
		['missing path', 'sdxl-anime', 'txt2img', null],
		['empty preset', '', 'txt2img', 'advanced'],
		['empty mode', 'sdxl-anime', '', 'advanced'],
		['empty path', 'sdxl-anime', 'txt2img', '']
	])('is null for %s', (_label, preset, mode, path) => {
		expect(buildSectionStorageKey(preset, mode, path)).toBeNull();
	});
});

describe('resolveSectionCollapsed', () => {
	it('prefers a remembered value over the YAML default, in either direction', () => {
		expect(resolveSectionCollapsed({ collapsed: true }, false)).toBe(false);
		expect(resolveSectionCollapsed({ collapsed: false }, true)).toBe(true);
	});

	it('falls back to config.collapsed when nothing is remembered yet', () => {
		expect(resolveSectionCollapsed({ collapsed: true }, undefined)).toBe(true);
		expect(resolveSectionCollapsed({ collapsed: false }, undefined)).toBe(false);
		expect(resolveSectionCollapsed({}, undefined)).toBe(false);
	});

	it('treats a missing config as not collapsed by default', () => {
		expect(resolveSectionCollapsed(null, undefined)).toBe(false);
		expect(resolveSectionCollapsed(undefined, undefined)).toBe(false);
	});
});

describe('foldedForScope', () => {
	const stored = {
		'sdxl-anime/txt2img/advanced': true,
		'sdxl-anime/txt2img/generation/sampling': false,
		'sdxl-anime/img2vid/advanced': true,
		'krea2/txt2img/advanced': false
	};

	it('re-keys only the matching preset+mode entries by bare fieldPath', () => {
		expect(foldedForScope(stored, 'sdxl-anime', 'txt2img')).toEqual({
			advanced: true,
			'generation/sampling': false
		});
	});

	it('keeps nested paths intact rather than splitting on every slash', () => {
		expect(foldedForScope(stored, 'sdxl-anime', 'txt2img')['generation/sampling']).toBe(false);
	});

	it('never leaks another preset or mode into scope', () => {
		expect(foldedForScope(stored, 'krea2', 'txt2img')).toEqual({ advanced: false });
		expect(foldedForScope(stored, 'sdxl-anime', 'img2vid')).toEqual({ advanced: true });
	});

	it('is empty when the scope or the map is incomplete', () => {
		expect(foldedForScope(stored, null, 'txt2img')).toEqual({});
		expect(foldedForScope(stored, 'sdxl-anime', undefined)).toEqual({});
		expect(foldedForScope(undefined, 'sdxl-anime', 'txt2img')).toEqual({});
		expect(foldedForScope({}, 'sdxl-anime', 'txt2img')).toEqual({});
	});

	it('returns a new object per call without mutating the stored map', () => {
		const snapshot = { ...stored };
		foldedForScope(stored, 'sdxl-anime', 'txt2img').advanced = false;
		expect(stored).toEqual(snapshot);
	});
});
