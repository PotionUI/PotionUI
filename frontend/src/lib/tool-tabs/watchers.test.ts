import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { isToolTab } from '$lib/types/tabs';
import { registerToolKind } from './registry';
import { startToolTabWatchers } from './watchers';
import { sameStatus, setToolTabStatus, statusCountLabel } from './status';
import type { ToolTabWatchContext } from './types';

describe('background tool tab watchers', () => {
	const started: string[] = [];
	const stopped: string[] = [];
	let contexts: ToolTabWatchContext[] = [];
	let unregister: () => void = () => {};
	let stop: () => void = () => {};

	beforeEach(() => {
		tabsStore.reset();
		started.length = 0;
		stopped.length = 0;
		contexts = [];
		unregister = registerToolKind({
			tool: 'demo.watch',
			label: 'Watched',
			icon: 'flask',
			accepts: [],
			pluginId: 'demo',
			watch: (context) => {
				started.push(context.tab.id);
				contexts.push(context);
				return () => stopped.push(context.tab.id);
			}
		});
	});

	afterEach(() => {
		stop();
		unregister();
	});

	it('watches only the tool tabs that are not active', () => {
		const first = tabsStore.openToolTab('demo.watch', null, { key: 'a' });
		const second = tabsStore.openToolTab('demo.watch', null, { key: 'b' });
		stop = startToolTabWatchers();
		expect(started).toEqual([first]);
		tabsStore.setActiveTab(first);
		expect(stopped).toEqual([first]);
		expect(started).toEqual([first, second]);
		tabsStore.removeTab(second);
		expect(stopped).toEqual([first, second]);
	});

	it('lets a watcher report the status of its tab, and stops every watcher on teardown', () => {
		const watched = tabsStore.openToolTab('demo.watch', null, { key: 'a' });
		tabsStore.openToolTab('demo.watch', null, { key: 'b' });
		stop = startToolTabWatchers();
		contexts[0].setStatus({ tone: 'success', label: 'Brew done' });
		const tab = get(tabsStore).tabs.find((item) => item.id === watched);
		expect(isToolTab(tab) && tab.status).toEqual({ tone: 'success', label: 'Brew done' });
		stop();
		stop = () => {};
		expect(stopped).toEqual([watched]);
	});

	it('writes a status only when it changed', () => {
		const id = tabsStore.openToolTab('demo.watch', null, { key: 'a' });
		const update = vi.spyOn(tabsStore, 'updateToolTab');
		setToolTabStatus(id, { tone: 'signal', label: 'Brewing', count: [1, 3], pulse: true });
		setToolTabStatus(id, { tone: 'signal', label: 'Brewing', count: [1, 3], pulse: true });
		setToolTabStatus(id, { tone: 'signal', label: 'Brewing', count: [2, 3], pulse: true });
		expect(update).toHaveBeenCalledTimes(2);
		update.mockRestore();
		expect(sameStatus(null, undefined)).toBe(true);
		expect(sameStatus({ tone: 'danger', label: 'x' }, null)).toBe(false);
		expect(statusCountLabel({ tone: 'signal', label: 'x', count: [3, 6] })).toBe('3/6');
		expect(statusCountLabel(null)).toBeNull();
	});
});
