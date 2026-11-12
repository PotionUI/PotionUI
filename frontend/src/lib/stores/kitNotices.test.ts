import { beforeEach, describe, expect, it } from 'vitest';
import { get, writable } from 'svelte/store';
import { effectiveNotices, kitNotices, noticesForTab, pruneKitNotices, setLiveKitNotices, setSubmitKitNotices } from './kitNotices';
import type { KitNotice } from '$lib/kits/types';

const n = (item: string): KitNotice => ({ code: 'unsupported_kind', message: item, kit: 'mara', item, kind: 'audio', field: 'refs' });

beforeEach(() => kitNotices.set({}));

describe('kit notices per tab', () => {
	it('keeps two tabs apart', () => {
		const tab = writable<string | null>('A');
		const view = noticesForTab(tab);
		setLiveKitNotices('A', [n('voice')]);
		setLiveKitNotices('B', [n('engine')]);
		expect(get(view).map((x) => x.item)).toEqual(['voice']);
		tab.set('B');
		expect(get(view).map((x) => x.item)).toEqual(['engine']);
		tab.set('C');
		expect(get(view)).toEqual([]);
	});

	it('keeps the live result and the after-submit result as separate fields, live first', () => {
		setSubmitKitNotices('A', [n('old')]);
		expect(effectiveNotices(get(kitNotices).A).map((x) => x.item)).toEqual(['old']);
		setLiveKitNotices('A', [n('new')]);
		expect(effectiveNotices(get(kitNotices).A).map((x) => x.item)).toEqual(['new']);
		setLiveKitNotices('A', null);
		expect(get(kitNotices).A.submit.map((x) => x.item)).toEqual(['old']);
		expect(effectiveNotices(get(kitNotices).A).map((x) => x.item)).toEqual(['old']);
	});

	it('forgets a closed tab', () => {
		setLiveKitNotices('A', [n('a')]);
		setLiveKitNotices('B', [n('b')]);
		pruneKitNotices(['B']);
		expect(Object.keys(get(kitNotices))).toEqual(['B']);
	});

	it('ignores an empty tab id', () => {
		setLiveKitNotices('', [n('a')]);
		expect(get(kitNotices)).toEqual({});
	});
});
