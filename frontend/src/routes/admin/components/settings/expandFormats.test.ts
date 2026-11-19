import { describe, expect, it } from 'vitest';
import { addFormat, asFormats, normalRatio, ratioOk, removeFormat, slugOf, MAX_HOUSE_FORMATS } from './expandFormats';

describe('house expand formats', () => {
	it('reads only well-formed entries', () => {
		expect(asFormats([{ id: 'a', label: 'A', ratio: '5:2', group_id: 'g' }, { id: 1 }, null, 'x', { id: 'b', label: 'B', ratio: '1:1' }])).toEqual([
			{ id: 'a', label: 'A', ratio: '5:2', group_id: 'g' },
			{ id: 'b', label: 'B', ratio: '1:1', group_id: null }
		]);
		expect(asFormats(undefined)).toEqual([]);
	});

	it('accepts whole-number ratios the editor can use', () => {
		expect(ratioOk('5:2')).toBe(true);
		expect(ratioOk(' 16 : 9 ')).toBe(true);
		expect(ratioOk('100:1')).toBe(false);
		expect(ratioOk('0:1')).toBe(false);
		expect(ratioOk('1.5:1')).toBe(false);
		expect(normalRatio(' 16 : 9 ')).toBe('16:9');
	});

	it('adds a format under a unique slug and removes it again', () => {
		let list = addFormat([], 'Team banner', '5:2', 'marketing');
		list = addFormat(list, 'Team banner', '3:1', null);
		expect(list.map((entry) => [entry.id, entry.ratio, entry.group_id])).toEqual([
			['team-banner', '5:2', 'marketing'],
			['team-banner-2', '3:1', null]
		]);
		expect(removeFormat(list, 'team-banner').map((entry) => entry.id)).toEqual(['team-banner-2']);
		expect(slugOf('   ', [])).toBe('format');
	});

	it('ignores an empty name, a bad ratio and a full list', () => {
		expect(addFormat([], ' ', '5:2', null)).toEqual([]);
		expect(addFormat([], 'X', 'wide', null)).toEqual([]);
		const full = Array.from({ length: MAX_HOUSE_FORMATS }, (_, index) => ({ id: `f${index}`, label: 'F', ratio: '1:1', group_id: null }));
		expect(addFormat(full, 'One more', '2:1', null)).toHaveLength(MAX_HOUSE_FORMATS);
	});
});
