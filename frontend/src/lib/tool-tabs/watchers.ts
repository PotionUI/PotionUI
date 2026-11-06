import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { isToolTab } from '$lib/types/tabs';
import { getToolKind, toolKinds } from './registry';
import { setToolTabStatus } from './status';

interface Running {
	signature: string;
	stop: () => void;
}

export function startToolTabWatchers(): () => void {
	const running = new Map<string, Running>();
	let started = false;

	const sync = () => {
		if (!started) return;
		const state = get(tabsStore);
		const wanted = new Map<string, string>();
		for (const tab of state.tabs) {
			if (!isToolTab(tab) || tab.id === state.activeTabId || !getToolKind(tab.tool)?.watch) continue;
			wanted.set(tab.id, `${tab.tool}|${tab.key ?? ''}`);
		}
		for (const [tabId, entry] of running) {
			if (wanted.get(tabId) === entry.signature) continue;
			running.delete(tabId);
			entry.stop();
		}
		for (const [tabId, signature] of wanted) {
			if (running.has(tabId)) continue;
			const tab = get(tabsStore).tabs.find((item) => item.id === tabId);
			const watch = isToolTab(tab) ? getToolKind(tab.tool)?.watch : undefined;
			if (!isToolTab(tab) || !watch) continue;
			running.set(tabId, { signature, stop: watch({ tab, setStatus: (status) => setToolTabStatus(tabId, status) }) });
		}
	};

	started = true;
	const stopTabs = tabsStore.subscribe(sync);
	const stopKinds = toolKinds.subscribe(sync);
	return () => {
		started = false;
		stopTabs();
		stopKinds();
		for (const entry of running.values()) entry.stop();
		running.clear();
	};
}
