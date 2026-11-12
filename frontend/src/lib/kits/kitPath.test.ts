import { describe, expect, it } from 'vitest';
import { formatKitPath, kitPathLabel, parseKitPath, parseKitQuery } from './kitPath';

describe('kit paths', () => {
	it('formats a kit and an item', () => {
		expect(formatKitPath('mara')).toBe('@kits.mara');
		expect(formatKitPath('mara', 'face')).toBe('@kits.mara.face');
	});

	it('formats the short label a chip shows', () => {
		expect(kitPathLabel('mara', 'face')).toBe('mara.face');
		expect(kitPathLabel('mara')).toBe('mara');
	});

	it('parses both forms back', () => {
		expect(parseKitPath('@kits.mara')).toEqual({ handle: 'mara', item: null });
		expect(parseKitPath('@kits.mara.face')).toEqual({ handle: 'mara', item: 'face' });
	});

	it('refuses anything else', () => {
		expect(parseKitPath('@kit.mara')).toBeNull();
		expect(parseKitPath('@kits.Mara')).toBeNull();
		expect(parseKitPath('@kits.mara.face.x')).toBeNull();
	});
});

describe('parseKitQuery', () => {
	it('ignores a query that is not for kits', () => {
		expect(parseKitQuery('image')).toBeNull();
	});

	it('knows the kit list level', () => {
		expect(parseKitQuery('kits')).toEqual({ handle: null, item: null, drilled: false });
		expect(parseKitQuery('kits.')).toEqual({ handle: '', item: null, drilled: false });
		expect(parseKitQuery('kits.ma')).toEqual({ handle: 'ma', item: null, drilled: false });
	});

	it('knows the item level', () => {
		expect(parseKitQuery('kits.mara.')).toEqual({ handle: 'mara', item: '', drilled: true });
		expect(parseKitQuery('kits.mara.fa')).toEqual({ handle: 'mara', item: 'fa', drilled: true });
	});
});
