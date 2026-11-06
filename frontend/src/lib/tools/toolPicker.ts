import { openMediaTool, type MediaToolResultInfo } from '$lib/media-tools/open';
import type { MediaToolItem as ToolResultItem } from '$lib/media-tools/types';
import { openToolShortcut } from '$lib/tool-tabs/openShortcut';
import type { MediaTool, MediaToolContext } from './tools';

export interface ToolPickHandlers {
	mount: (tool: MediaTool, context: MediaToolContext) => void;
	onResult: (
		items: ToolResultItem[],
		context: MediaToolContext,
		info: MediaToolResultInfo
	) => void | Promise<void>;
}

export function pickTool(tool: MediaTool, context: MediaToolContext, handlers: ToolPickHandlers): void {
	if (tool.source === 'core' && openToolShortcut(tool.id, context.items)) return;
	if (!tool.catalogToolId) {
		handlers.mount(tool, context);
		return;
	}
	void openMediaTool({
		toolId: tool.catalogToolId,
		items: context.items,
		scope: context.scope,
		context,
		announce: context.scope === 'history' ? false : undefined,
		onResult: (items, info) => handlers.onResult(items, context, info)
	});
}
