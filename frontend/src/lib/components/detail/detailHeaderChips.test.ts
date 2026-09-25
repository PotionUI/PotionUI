import { describe, it, expect } from 'vitest';
import { splitDetailHeaderChips, type DetailHeaderChip } from './detailHeaderChips';

function chip(key: string): DetailHeaderChip {
	return { key, label: key };
}

describe('splitDetailHeaderChips', () => {
	it('keeps every chip visible when at or under the max', () => {
		const chips = [chip('a'), chip('b'), chip('c')];
		expect(splitDetailHeaderChips(chips)).toEqual({ visible: chips, overflow: [] });
	});

	it('folds anything past the max into overflow', () => {
		const chips = [chip('a'), chip('b'), chip('c'), chip('d'), chip('e')];
		const result = splitDetailHeaderChips(chips);
		expect(result.visible.map((c) => c.key)).toEqual(['a', 'b', 'c']);
		expect(result.overflow.map((c) => c.key)).toEqual(['d', 'e']);
	});

	it('honors a custom max', () => {
		const chips = [chip('a'), chip('b'), chip('c')];
		const result = splitDetailHeaderChips(chips, 1);
		expect(result.visible.map((c) => c.key)).toEqual(['a']);
		expect(result.overflow.map((c) => c.key)).toEqual(['b', 'c']);
	});

	it('returns no overflow for an empty list', () => {
		expect(splitDetailHeaderChips([])).toEqual({ visible: [], overflow: [] });
	});
});
