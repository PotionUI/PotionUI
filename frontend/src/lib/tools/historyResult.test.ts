import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

const goto = vi.fn().mockResolvedValue(undefined);
const show = vi.fn();
vi.mock('$app/navigation', () => ({ goto: (...args: unknown[]) => goto(...args) }));
vi.mock('$lib/stores/toast', () => ({ toasts: { show: (...args: unknown[]) => show(...args) } }));

import { decideLanding, landOnHistoryResult, pluralNoun, type LandingEnv } from './historyResult';
import { libraryModalRequest, closeLibraryResultModal } from '$lib/library/landing';
import { takePendingFresh } from '$lib/library/freshHandoff';

const clear: LandingEnv = { overlayOpen: false, typing: false, samePage: true };
const item = (id: string, type = 'image') => ({ id, filename: `${id}.png`, media_type: type, url: `/${id}` }) as never;
const source = { generationId: 'g1', fileIndex: 2 };

beforeEach(() => {
	goto.mockClear();
	show.mockClear();
	closeLibraryResultModal();
	takePendingFresh();
});

describe('decideLanding', () => {
	it('shows nothing for no result, as after a failure or a cancel', () => {
		expect(decideLanding(0, clear)).toEqual({ kind: 'none' });
	});

	it('opens the modal for one result when the user is still where they started', () => {
		expect(decideLanding(1, clear)).toEqual({ kind: 'modal' });
	});

	it('falls back to a toast for one result when another modal, editor or menu is open', () => {
		expect(decideLanding(1, { ...clear, overlayOpen: true })).toEqual({ kind: 'toast-open' });
	});

	it('falls back to a toast for one result while a field is focused', () => {
		expect(decideLanding(1, { ...clear, typing: true })).toEqual({ kind: 'toast-open' });
	});

	it('falls back to a toast for one result when the user has changed page', () => {
		expect(decideLanding(1, { ...clear, samePage: false })).toEqual({ kind: 'toast-open' });
	});

	it('shows the split toast for several results, whatever else is open', () => {
		expect(decideLanding(3, clear)).toEqual({ kind: 'toast-split' });
		expect(decideLanding(3, { ...clear, overlayOpen: true })).toEqual({ kind: 'toast-split' });
	});
});

describe('landOnHistoryResult', () => {
	it('opens the Library modal over History with the source generation for one result, with no navigation and no toast', () => {
		landOnHistoryResult([item('lib-1')], { source, startPath: '/history', env: clear });

		const request = get(libraryModalRequest);
		expect(request?.item.id).toBe('lib-1');
		expect(request?.source).toEqual(source);
		expect(goto).not.toHaveBeenCalled();
		expect(show).not.toHaveBeenCalled();
		expect(takePendingFresh()).toEqual([]);
	});

	it('toasts Saved to Library with an Open action, and tags it new, when the modal would be in the way', () => {
		landOnHistoryResult([item('lib-1')], { source, startPath: '/history', env: { ...clear, overlayOpen: true } });

		expect(get(libraryModalRequest)).toBeNull();
		expect(show).toHaveBeenCalledTimes(1);
		const [, message, options] = show.mock.calls[0];
		expect(message).toBe('Saved to Library');
		expect(options.action.label).toBe('Open');
		expect(takePendingFresh()).toEqual(['lib-1']);
	});

	it('opens the modal from the Open action and clears the new tag', () => {
		landOnHistoryResult([item('lib-1')], { source, startPath: '/history', env: { ...clear, typing: true } });

		show.mock.calls[0][2].action.onClick();

		expect(get(libraryModalRequest)?.item.id).toBe('lib-1');
		expect(takePendingFresh()).toEqual([]);
	});

	it('stays put with a toast naming the count and kind, and tags every item new, for several results', () => {
		landOnHistoryResult([item('a', 'video'), item('b', 'video'), item('c', 'video')], {
			source,
			startPath: '/history',
			env: clear
		});

		expect(get(libraryModalRequest)).toBeNull();
		expect(goto).not.toHaveBeenCalled();
		const [, message, options] = show.mock.calls[0];
		expect(message).toBe('3 clips saved to Library, tagged New');
		expect(options.action.label).toBe('Open Library');
		expect(takePendingFresh()).toEqual(['a', 'b', 'c']);

		options.action.onClick();
		expect(goto).toHaveBeenCalledWith('/library');
	});

	it('does nothing for no result', () => {
		landOnHistoryResult([], { source, startPath: '/history', env: clear });

		expect(get(libraryModalRequest)).toBeNull();
		expect(show).not.toHaveBeenCalled();
	});
});

describe('pluralNoun', () => {
	it('names the kind, and falls back to items for a mix', () => {
		expect(pluralNoun([item('a', 'video')])).toBe('clips');
		expect(pluralNoun([item('a', 'image')])).toBe('images');
		expect(pluralNoun([item('a', 'audio')])).toBe('tracks');
		expect(pluralNoun([item('a', 'image'), item('b', 'video')])).toBe('items');
	});
});
