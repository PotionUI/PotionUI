import { describe, expect, it } from 'vitest';
import {
	addKitRow,
	encodeKitKey,
	isKitRow,
	parseKitKey,
	removeKitRow,
	snapshotOf,
	splitKitRows,
	withKitRows
} from './kitMarker';

describe('kit keys', () => {
	it('round-trips an item citation', () => {
		const key = encodeKitKey('kt_7f3a0001', 'it_41c90002');
		expect(key).toBe('kit:kt_7f3a0001/it_41c90002');
		expect(parseKitKey(key)).toEqual({ kitId: 'kt_7f3a0001', itemId: 'it_41c90002' });
	});

	it('round-trips a bare kit', () => {
		expect(parseKitKey(encodeKitKey('kt_7f3a0001'))).toEqual({ kitId: 'kt_7f3a0001', itemId: null });
	});

	it('does not claim a model or media key', () => {
		expect(parseKitKey('model:abc')).toBeNull();
		expect(parseKitKey('uploads/a.png')).toBeNull();
		expect(parseKitKey('kit:nonsense')).toBeNull();
	});
});

describe('kit rows in a loader value', () => {
	const media = { path: 'a.png', url: '/a.png' };
	const kit = { kit: 'kt_7f3a0001' };

	it('recognises a kit row and not a media row', () => {
		expect(isKitRow(kit)).toBe(true);
		expect(isKitRow(media)).toBe(false);
		expect(isKitRow({ model: 'x' })).toBe(false);
		expect(isKitRow(null)).toBe(false);
	});

	it('splits and rejoins without losing order within each side', () => {
		const { media: m, kits } = splitKitRows([media, kit, { path: 'b.png', url: '/b.png' }]);
		expect(m).toHaveLength(2);
		expect(kits).toEqual([kit]);
		expect(withKitRows(m, kits)).toEqual([media, { path: 'b.png', url: '/b.png' }, kit]);
	});

	it('adds a kit once and removes it by id', () => {
		expect(addKitRow([kit], kit)).toEqual([kit]);
		expect(addKitRow([], kit)).toEqual([kit]);
		expect(removeKitRow([kit], 'kt_7f3a0001')).toEqual([]);
	});

	it('snapshots names for the session notice', () => {
		expect(snapshotOf({ name: 'Mara', handle: 'mara', items: [{ id: 'it_1', name: 'face' }] })).toEqual({
			name: 'Mara',
			handle: 'mara',
			items: [{ id: 'it_1', name: 'face' }]
		});
	});
});
