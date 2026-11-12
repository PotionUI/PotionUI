import { describe, expect, it } from 'vitest';
import { kitUseSentence, kitsUsingUploads } from './kitUsage';

const row = (kit: string) => ({ kit_id: kit, kit_name: kit, handle: kit, item_id: 'it_1', item_name: 'a' });

describe('kitsUsingUploads', () => {
	it('counts distinct kits across files', () => {
		expect(
			kitsUsingUploads({
				a: { count: 2, items: [row('kt_1'), row('kt_2')] },
				b: { count: 1, items: [row('kt_1')] },
				c: { count: 0, items: [] }
			})
		).toBe(2);
	});

	it('is zero without data', () => {
		expect(kitsUsingUploads(null)).toBe(0);
		expect(kitsUsingUploads({})).toBe(0);
	});
});

describe('kitUseSentence', () => {
	it('says nothing when no kit uses the file', () => {
		expect(kitUseSentence(0)).toBe('');
	});

	it('names the count for one file and for several', () => {
		expect(kitUseSentence(1)).toBe(' It is used in 1 kit; that kit item will show "File deleted".');
		expect(kitUseSentence(2, true)).toBe(' Some of them are used in 2 kits; those kit items will show "File deleted".');
	});
});
