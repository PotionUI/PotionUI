import { describe, expect, it } from 'vitest';
import { FULL_ID_MAX, middleEllipsis } from './idDisplay';

describe('middleEllipsis', () => {
	it('keeps an id that fits whole', () => {
		const ulid = '01JF7KXQ4M8VT2N3P5R6S7T8W9';
		expect(ulid).toHaveLength(FULL_ID_MAX);
		expect(middleEllipsis(ulid)).toBe(ulid);
	});

	it('keeps start and end of a longer id within the limit', () => {
		const uuid = '3f2b8c1e-9d4a-4e7b-a1c2-5d6e7f8a9b0c';
		const shown = middleEllipsis(uuid);
		expect(shown).toHaveLength(FULL_ID_MAX);
		expect(shown.startsWith('3f2b8c1e-9d4a')).toBe(true);
		expect(shown.endsWith('8a9b0c')).toBe(true);
		expect(shown).toContain('…');
	});

	it('handles empty input', () => {
		expect(middleEllipsis('')).toBe('');
	});
});
