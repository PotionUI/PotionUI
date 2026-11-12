import { describe, expect, it } from 'vitest';
import {
	activeFilterCount,
	clearSource,
	datePresetRange,
	effectiveKind,
	freshFilters,
	hasFiltersButton,
	pagesFor,
	popoverFilterCount,
	rangeText,
	tierForWidth,
	toHistoryRequest,
	toKitRequest,
	toLibraryRequest
} from './pickerModel';

const NOW = new Date(2026, 10, 12, 15, 30);
const context = { kinds: ['image', 'video'] as const, page: 1, pageSize: 20, now: NOW };

describe('tiers', () => {
	it('uses the 1280 and 768 breakpoints', () => {
		expect(tierForWidth(1440)).toBe('xl');
		expect(tierForWidth(1280)).toBe('xl');
		expect(tierForWidth(1279)).toBe('md');
		expect(tierForWidth(1024)).toBe('md');
		expect(tierForWidth(768)).toBe('md');
		expect(tierForWidth(767)).toBe('sm');
		expect(tierForWidth(390)).toBe('sm');
	});
});

describe('date presets', () => {
	it('resolves each preset against a fixed clock', () => {
		expect(datePresetRange('today', NOW)).toEqual({ from: '2026-11-12', to: '2026-11-12' });
		expect(datePresetRange('yesterday', NOW)).toEqual({ from: '2026-11-11', to: '2026-11-11' });
		expect(datePresetRange('last_week', NOW)).toEqual({ from: '2026-11-05', to: '2026-11-12' });
		expect(datePresetRange('last_month', NOW)).toEqual({ from: '2026-10-12', to: '2026-11-12' });
		expect(datePresetRange('all', NOW)).toEqual({ from: null, to: null });
	});
});

describe('History request', () => {
	it('sends only what is set, with completed status and tags included', () => {
		expect(toHistoryRequest(freshFilters().history, context)).toEqual({
			limit: 20,
			offset: 0,
			status: 'completed',
			includeTags: true
		});
	});

	it('maps every history filter to the list route\'s own parameter', () => {
		const filters = {
			...freshFilters().history,
			search: '  harbour  ',
			kind: 'video' as const,
			favorites: true,
			collectionId: 'c1',
			tagIds: ['t1', 't2'],
			date: 'last_week' as const,
			model: 'Flux 3',
			presetId: 'p1',
			sort: 'largest' as const
		};
		expect(toHistoryRequest(filters, { ...context, page: 3, pageSize: 50 })).toEqual({
			limit: 50,
			offset: 100,
			status: 'completed',
			includeTags: true,
			mediaType: 'video',
			createdFrom: '2026-11-05',
			createdTo: '2026-11-12',
			search: 'harbour',
			favoritesOnly: true,
			presetId: 'p1',
			modelName: 'Flux 3',
			tagIds: ['t1', 't2'],
			collectionId: 'c1',
			sortBy: 'file_size',
			sortDir: 'desc'
		});
	});

	it('uses a custom date range as typed and omits the default sort', () => {
		const request = toHistoryRequest({ ...freshFilters().history, date: 'custom', dateFrom: '2026-01-02', dateTo: '' }, context);
		expect(request.createdFrom).toBe('2026-01-02');
		expect(request.createdTo).toBeUndefined();
		expect(request.sortBy).toBeUndefined();
		expect(toHistoryRequest({ ...freshFilters().history, sort: 'rated' }, context)).toMatchObject({ sortBy: 'rating', sortDir: 'desc' });
		expect(toHistoryRequest({ ...freshFilters().history, sort: 'oldest' }, context)).toMatchObject({ sortBy: 'created_at', sortDir: 'asc' });
	});

	it('pins the media type when the caller fixes one kind', () => {
		expect(toHistoryRequest(freshFilters().history, { ...context, kinds: ['image'] }).mediaType).toBe('image');
		expect(effectiveKind('all', ['image', 'video'])).toBeNull();
		expect(effectiveKind('video', ['image'])).toBe('video');
	});
});

describe('Library request', () => {
	it('builds the same query the Library page builds', () => {
		expect(toLibraryRequest(freshLibrary(), { kinds: ['image', 'video'], page: 2, pageSize: 10 })).toEqual({ limit: 10, offset: 10 });
		expect(
			toLibraryRequest(
				{ ...freshLibrary(), search: 'portrait', kind: 'video', favorites: true, collectionId: 'c1', tagIds: ['t1'] },
				{ kinds: ['image', 'video'], page: 1, pageSize: 20 }
			)
		).toEqual({ limit: 20, offset: 0, media_type: 'video', tag_ids: 't1', collection_id: 'c1', favorites_only: true, search: 'portrait' });
	});

	it('sends Unsorted as its own flag and a fixed kind as media_type', () => {
		expect(toLibraryRequest({ ...freshLibrary(), unsorted: true }, { kinds: ['audio'], page: 1, pageSize: 20 })).toMatchObject({ unsorted: true, media_type: 'audio' });
	});

	it('does not send a 3D kind the Library cannot hold', () => {
		expect(toLibraryRequest(freshLibrary(), { kinds: ['mesh'], page: 1, pageSize: 20 }).media_type).toBeUndefined();
	});
});

function freshLibrary() {
	return freshFilters().library;
}

describe('Kits request', () => {
	it('sends search, label and page with the fixed kit page size', () => {
		expect(toKitRequest({ search: ' her ', label: 'Actor' }, 2)).toEqual({ search: 'her', label: 'Actor', page: 2, page_size: 20 });
		expect(toKitRequest({ search: '', label: null }, 1)).toEqual({ search: '', label: null, page: 1, page_size: 20 });
	});
});

describe('active and popover counts', () => {
	it('counts each active filter once and ignores a kind switch the caller cannot see', () => {
		const filters = freshFilters();
		expect(activeFilterCount('history', filters, ['image'])).toBe(0);
		filters.history = { ...filters.history, kind: 'video', search: 'a', tagIds: ['t1', 't2'], date: 'today', model: 'm', presetId: 'p', favorites: true, collectionId: 'c' };
		expect(activeFilterCount('history', filters, ['image'])).toBe(8);
		expect(activeFilterCount('history', filters, ['image', 'video'])).toBe(9);
		filters.library = { ...filters.library, unsorted: true, tagIds: ['t'], search: 's' };
		expect(activeFilterCount('library', filters, ['image'])).toBe(3);
		filters.kits = { search: 'k', label: 'Actor' };
		expect(activeFilterCount('kits', filters, ['image'])).toBe(2);
	});

	it('counts only what lives inside the popover at each tier', () => {
		const filters = freshFilters();
		filters.history = { ...filters.history, date: 'today', model: 'm', collectionId: 'c', tagIds: ['t'], favorites: true, sort: 'oldest', kind: 'video' };
		expect(popoverFilterCount('history', filters, 'xl', ['image', 'video'])).toBe(2);
		expect(popoverFilterCount('history', filters, 'md', ['image', 'video'])).toBe(4);
		expect(popoverFilterCount('history', filters, 'sm', ['image', 'video'])).toBe(7);
		expect(popoverFilterCount('history', filters, 'sm', ['image'])).toBe(6);
		filters.kits = { search: '', label: 'Actor' };
		expect(popoverFilterCount('kits', filters, 'md', ['image'])).toBe(0);
		expect(popoverFilterCount('kits', filters, 'sm', ['image'])).toBe(1);
	});

	it('shows the Filters button where the row cannot hold everything', () => {
		expect(hasFiltersButton('history', 'xl')).toBe(true);
		expect(hasFiltersButton('library', 'xl')).toBe(false);
		expect(hasFiltersButton('library', 'md')).toBe(true);
		expect(hasFiltersButton('kits', 'md')).toBe(false);
		expect(hasFiltersButton('kits', 'sm')).toBe(true);
	});

	it('clears only the active source', () => {
		const filters = freshFilters();
		filters.history = { ...filters.history, search: 'a' };
		filters.library = { ...filters.library, search: 'b' };
		const next = clearSource('history', filters);
		expect(next.history.search).toBe('');
		expect(next.library.search).toBe('b');
	});
});

describe('paging text', () => {
	it('computes pages and the showing range', () => {
		expect(pagesFor(0, 20)).toBe(1);
		expect(pagesFor(41, 20)).toBe(3);
		expect(rangeText(1, 20, 134)).toBe('Showing 1–20 of 134');
		expect(rangeText(7, 20, 134)).toBe('Showing 121–134 of 134');
		expect(rangeText(1, 20, 0)).toBe('');
	});
});
