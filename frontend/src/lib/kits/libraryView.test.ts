import { describe, expect, it } from 'vitest';
import { hrefWithKitsView, kitsViewFromUrl } from './libraryView';

const url = (search: string) => new URL(`http://x/library${search}`);

describe('kitsViewFromUrl', () => {
	it('is off by default', () => {
		expect(kitsViewFromUrl(url(''))).toEqual({ active: false, kitId: null });
	});

	it('reads the list and one open kit', () => {
		expect(kitsViewFromUrl(url('?view=kits'))).toEqual({ active: true, kitId: null });
		expect(kitsViewFromUrl(url('?view=kits&kit=kt_7f3a0001'))).toEqual({ active: true, kitId: 'kt_7f3a0001' });
	});

	it('ignores a kit id when the view is not kits, or when it is unsafe', () => {
		expect(kitsViewFromUrl(url('?kit=kt_7f3a0001')).active).toBe(false);
		expect(kitsViewFromUrl(url('?view=kits&kit=../x')).kitId).toBeNull();
	});
});

describe('hrefWithKitsView', () => {
	it('turns the view on and off while keeping other params', () => {
		expect(hrefWithKitsView(url('?item=u1'), { active: true, kitId: null })).toBe('/library?item=u1&view=kits');
		expect(hrefWithKitsView(url('?view=kits&kit=kt_1&item=u1'), { active: false, kitId: null })).toBe('/library?item=u1');
	});

	it('sets and clears the open kit', () => {
		expect(hrefWithKitsView(url('?view=kits'), { active: true, kitId: 'kt_7f3a0001' })).toBe('/library?view=kits&kit=kt_7f3a0001');
		expect(hrefWithKitsView(url('?view=kits&kit=kt_7f3a0001'), { active: true, kitId: null })).toBe('/library?view=kits');
	});
});
