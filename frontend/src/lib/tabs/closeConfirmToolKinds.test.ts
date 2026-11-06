import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { registerToolKind } from '$lib/tool-tabs/registry';
import { confirmCloseTab, pendingTabClose, requestCloseTab } from './closeConfirm';

describe('closing tool tabs whose kind needs no confirmation', () => {
	const onClosed = vi.fn();
	let unregister: () => void = () => {};

	beforeEach(() => {
		tabsStore.reset();
		onClosed.mockReset();
		unregister = registerToolKind({ tool: 'demo.quiet', label: 'Quiet', icon: 'flask', accepts: [], pluginId: 'demo', confirmClose: false, onClosed });
	});

	afterEach(() => unregister());

	it('closes at once and tells the kind', () => {
		const id = tabsStore.openToolTab('demo.quiet', null, { key: 'a' });
		requestCloseTab(id);
		expect(get(pendingTabClose)).toBeNull();
		expect(get(tabsStore).tabs.some((tab) => tab.id === id)).toBe(false);
		expect(onClosed).toHaveBeenCalledWith(expect.objectContaining({ id, key: 'a' }));
	});

	it('still asks when the tab has unsaved edits, and tells the kind after the confirm', () => {
		const id = tabsStore.openToolTab('demo.quiet', null, { key: 'a' });
		tabsStore.updateToolTab(id, { dirty: 2 });
		requestCloseTab(id);
		expect(get(pendingTabClose)).toBe(id);
		expect(onClosed).not.toHaveBeenCalled();
		confirmCloseTab();
		expect(get(tabsStore).tabs.some((tab) => tab.id === id)).toBe(false);
		expect(onClosed).toHaveBeenCalledTimes(1);
	});

	it('keeps asking for other tool kinds', () => {
		const id = tabsStore.openToolTab('image', null);
		requestCloseTab(id);
		expect(get(pendingTabClose)).toBe(id);
		confirmCloseTab();
	});
});
