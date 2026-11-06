import { tabsStore } from '$lib/stores/tabs';
import type { MediaRef, RunRef, ToolTab } from '$lib/types/tabs';
import { setToolTabActions } from './actions';
import { rememberTool } from './lastTool';
import { setToolTabStatus } from './status';
import type { ToolTabActions, ToolTabHost, ToolTabOpenOptions, ToolTabStatus } from './types';

export function createToolTabHost(tab: ToolTab, phone: boolean): ToolTabHost {
	const tabId = tab.id;
	return {
		tabId,
		tool: tab.tool,
		source: tab.source,
		state: tab.state ?? null,
		activeTool: tab.activeTool ?? null,
		run: tab.run ?? null,
		hint: tab.hint ?? null,
		phone,
		key: tab.key ?? null,
		setState: (next: unknown) => tabsStore.updateToolTab(tabId, { state: next ?? null }),
		setDirty: (edits: number) => tabsStore.updateToolTab(tabId, { dirty: Math.max(0, Math.floor(edits || 0)) }),
		setTitle: (title: string) => tabsStore.setToolTabTitle(tabId, title.trim()),
		setThumbnail: (url: string | null) => tabsStore.updateToolTab(tabId, { thumbnail: url }),
		setSource: (source: MediaRef | null) => tabsStore.updateToolTab(tabId, { source }),
		replaceSource: (source: MediaRef) => tabsStore.replaceToolTabSource(tabId, source),
		setActiveTool: (activeTool: string | null) => {
			rememberTool(tab.tool, activeTool);
			tabsStore.updateToolTab(tabId, { activeTool });
		},
		setRun: (run: RunRef | null) => tabsStore.updateToolTab(tabId, { run }),
		clearHint: () => tabsStore.updateToolTab(tabId, { hint: null }),
		setActions: (actions: ToolTabActions | null) => setToolTabActions(tabId, actions),
		setStatus: (status: ToolTabStatus | null) => setToolTabStatus(tabId, status),
		claimKey: (key: string, state: unknown, title?: string | null) => tabsStore.claimToolTabKey(tabId, key, state, title ?? null),
		openToolTab: (tool: string, source: MediaRef | null, options?: ToolTabOpenOptions) =>
			tabsStore.openToolTab(tool, source, {
				activeTool: options?.activeTool ?? null,
				hint: options?.hint ?? null,
				key: options?.key ?? null,
				state: options?.state,
				name: options?.name ?? null
			})
	};
}
