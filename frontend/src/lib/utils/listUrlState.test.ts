import { describe, it, expect, vi, beforeEach } from 'vitest';

const gotoMock = vi.fn(async () => {});
vi.mock('$app/navigation', () => ({ goto: (...args: unknown[]) => gotoMock(...(args as [])) }));

const { pageParam, choiceParam, hrefWithParams, replaceListParams } = await import('./listUrlState');

const url = (search: string) => new URL(`http://x/library${search}`);

beforeEach(() => gotoMock.mockClear());

describe('pageParam', () => {
	it('defaults to the first page', () => {
		expect(pageParam(url('').searchParams)).toBe(1);
	});

	it('reads a valid page and rejects junk', () => {
		expect(pageParam(url('?page=3').searchParams)).toBe(3);
		expect(pageParam(url('?page=0').searchParams)).toBe(1);
		expect(pageParam(url('?page=-2').searchParams)).toBe(1);
		expect(pageParam(url('?page=abc').searchParams)).toBe(1);
		expect(pageParam(url('?p=4').searchParams, 'p')).toBe(4);
	});
});

describe('choiceParam', () => {
	const allowed = ['manual', 'name'] as const;

	it('returns an allowed value and falls back otherwise', () => {
		expect(choiceParam(url('?sort=name').searchParams, 'sort', allowed, 'manual')).toBe('name');
		expect(choiceParam(url('?sort=bogus').searchParams, 'sort', allowed, 'manual')).toBe('manual');
		expect(choiceParam(url('').searchParams, 'sort', allowed, 'manual')).toBe('manual');
	});
});

describe('hrefWithParams', () => {
	it('sets values, drops empty ones and keeps unrelated params and hash', () => {
		expect(hrefWithParams(url('?view=kits&page=2#top'), { page: 3, sort: 'name' })).toBe('/library?view=kits&page=3&sort=name#top');
		expect(hrefWithParams(url('?view=kits&page=2&q=a'), { page: null, q: '' })).toBe('/library?view=kits');
		expect(hrefWithParams(url('?page=2'), { page: undefined })).toBe('/library');
	});
});

describe('replaceListParams', () => {
	it('replaces history without stealing focus or scroll', async () => {
		await replaceListParams(url('?view=kits'), { page: 2 });
		expect(gotoMock).toHaveBeenCalledWith('/library?view=kits&page=2', { replaceState: true, keepFocus: true, noScroll: true });
	});

	it('does nothing when the URL would not change', async () => {
		await replaceListParams(url('?view=kits&page=2'), { page: 2 });
		expect(gotoMock).not.toHaveBeenCalled();
	});
});
