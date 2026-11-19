import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { editCloseLoss, setToolTabCloseGuard, type CloseLoss } from '$lib/tool-tabs/closeGuard';
import { describeTabClose, pendingTabClose, requestCloseTab } from './closeConfirm';
import { isToolTab } from '$lib/types/tabs';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('editCloseLoss', () => {
	it('loses nothing when the edits are saved on the server', () => {
		expect(editCloseLoss({ edits: 3, layersFlushed: true, status: 'saved' })).toBeNull();
	});

	it('loses nothing when there are no edits', () => {
		expect(editCloseLoss({ edits: 0, layersFlushed: true, status: 'idle' })).toBeNull();
	});

	it.each([
		['offline', 'offline'],
		['failed', 'failed'],
		['conflict', 'conflict'],
		['too-large', 'too-large'],
		['saving', 'unsynced'],
		['idle', 'unsynced']
	] as const)('loses the edits when autosave is %s', (status, reason) => {
		expect(editCloseLoss({ edits: 2, layersFlushed: true, status })).toEqual({ edits: 2, reason });
	});

	it('loses the work when a layer could not be uploaded, even if autosave says saved', () => {
		expect(editCloseLoss({ edits: 2, layersFlushed: false, status: 'saved' })).toEqual({ edits: 2, reason: 'layers' });
	});
});

describe('closing a tool tab with a close guard', () => {
	let id: string;

	beforeEach(() => {
		tabsStore.reset();
		tabsStore.openToolTab('image', null);
		id = tabsStore.openToolTab('image', null);
		tabsStore.updateToolTab(id, { dirty: 3 });
	});

	afterEach(() => setToolTabCloseGuard(id, null));

	it('closes without a prompt when the guard reports nothing is lost, whatever the tab dirty count says', async () => {
		setToolTabCloseGuard(id, async () => null);
		requestCloseTab(id);
		await flush();
		expect(get(pendingTabClose)).toBeNull();
		expect(get(tabsStore).tabs.some((tab) => tab.id === id)).toBe(false);
	});

	it('waits for the guard to flush before deciding', async () => {
		let release: () => void = () => {};
		setToolTabCloseGuard(id, () => new Promise<CloseLoss | null>((resolve) => (release = () => resolve(null))));
		requestCloseTab(id);
		await flush();
		expect(get(tabsStore).tabs.some((tab) => tab.id === id)).toBe(true);
		expect(get(pendingTabClose)).toBeNull();
		release();
		await flush();
		expect(get(tabsStore).tabs.some((tab) => tab.id === id)).toBe(false);
	});

	it('asks and says what is lost when the guard reports a loss', async () => {
		setToolTabCloseGuard(id, async () => ({ edits: 2, reason: 'offline' }));
		requestCloseTab(id);
		await flush();
		expect(get(pendingTabClose)).toBe(id);
		const tab = get(tabsStore).tabs.find((item) => item.id === id)!;
		expect(isToolTab(tab)).toBe(true);
		const described = describeTabClose(tab);
		expect(described.title).toBe('Discard 2 unsaved edits?');
		expect(described.message).toContain('2 edits will be lost');
		expect(described.message).toContain('offline');
		expect(described.confirmLabel).toBe('Discard and close');
	});
});
