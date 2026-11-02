import { tabsStore } from '$lib/stores/tabs';
import type { MediaRef, RunRef, ToolTab } from '$lib/types/tabs';
import { setToolTabActions } from './actions';
import { openToolTab } from './open';
import type { ToolTabActions, ToolTabHost, ToolTabOpenOptions } from './types';

export function createToolTabHost(tab: ToolTab, phone: boolean): ToolTabHost {
	const tabId = tab.id;
	return {
		tabId,
		tool: tab.tool,
		source: tab.source,
		state: tab.state ?? null,
		activeTool: tab.activeTool ?? null,
		run: tab.run ?? null,
		phone,
		setState: (next: unknown) => tabsStore.updateToolTab(tabId, { state: next ?? null }),
		setDirty: (edits: number) => tabsStore.updateToolTab(tabId, { dirty: Math.max(0, Math.floor(edits || 0)) }),
		setTitle: (title: string) => tabsStore.setToolTabTitle(tabId, title.trim()),
		setThumbnail: (url: string | null) => tabsStore.updateToolTab(tabId, { thumbnail: url }),
		setSource: (source: MediaRef | null) => tabsStore.updateToolTab(tabId, { source }),
		setActiveTool: (activeTool: string | null) => tabsStore.updateToolTab(tabId, { activeTool }),
		setRun: (run: RunRef | null) => tabsStore.updateToolTab(tabId, { run }),
		setActions: (actions: ToolTabActions | null) => setToolTabActions(tabId, actions),
		openToolTab: (tool: string, source: MediaRef | null, options?: ToolTabOpenOptions) =>
			openToolTab(tool, source, options)
	};
}
