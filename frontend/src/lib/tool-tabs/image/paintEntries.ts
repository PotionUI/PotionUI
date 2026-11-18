import PaintEditsGroup from '$lib/components/paint/PaintEditsGroup.svelte';
import { CORE_OWNER } from '$lib/registries/registry';
import { railGroups, railTools } from '$lib/editors/railTools';
import {
	PAINT_GROUP,
	PAINT_GROUP_LABEL,
	SELECT_GROUP,
	SELECT_GROUP_LABEL,
	builtinPaintTools,
	paintEntryId,
	paintGroupOf,
	paintIconFor
} from '$lib/paint/entries';
import { pluginTools } from '$lib/paint/registries';
import type { PaintTool } from '$lib/paint/types';
import { brushToolActions, selectionToolActions } from './canvasActions';

export const PAINT_FOLD = 5;
export const SELECT_FOLD = 3;

const PLUGIN_ORDER_BASE = 1000;
const pluginEntries = new Map<string, { tool: PaintTool; dispose: () => void }>();
let registered = false;

const SELECTION_TOOLS: ReadonlySet<string> = new Set(['rect', 'lasso', 'move']);
const BRUSH_TOOLS: ReadonlySet<string> = new Set(['brush', 'eraser']);

function canvasActionsFor(tool: PaintTool) {
	if (SELECTION_TOOLS.has(tool.id)) return { canvasActions: selectionToolActions };
	if (BRUSH_TOOLS.has(tool.id)) return { canvasActions: brushToolActions };
	return {};
}

function entryFor(tool: PaintTool, order: number) {
	return {
		id: paintEntryId(tool.id),
		editor: 'image' as const,
		group: paintGroupOf(tool),
		label: tool.label,
		icon: paintIconFor(tool),
		order,
		children: { component: PaintEditsGroup },
		...canvasActionsFor(tool),
		...(tool.key ? { keys: tool.key } : {})
	};
}

function syncPluginPaintEntries(): void {
	const builtin = new Set(builtinPaintTools().map((tool) => tool.id));
	const wanted = new Map(pluginTools.list().filter((tool) => typeof tool.label === 'string' && tool.label && !builtin.has(tool.id)).map((tool) => [tool.id, tool]));
	for (const [id, held] of pluginEntries) {
		if (wanted.get(id) === held.tool) continue;
		held.dispose();
		pluginEntries.delete(id);
	}
	let order = PLUGIN_ORDER_BASE;
	for (const [id, tool] of wanted) {
		order += 10;
		if (pluginEntries.has(id)) continue;
		pluginEntries.set(id, { tool, dispose: railTools.register(entryFor(tool, order), CORE_OWNER) });
	}
}

export function ensurePaintEntries(): void {
	if (registered) return;
	registered = true;
	railGroups.register({ id: PAINT_GROUP, editor: 'image', label: PAINT_GROUP_LABEL, order: 200, fold: PAINT_FOLD }, CORE_OWNER);
	railGroups.register({ id: SELECT_GROUP, editor: 'image', label: SELECT_GROUP_LABEL, order: 210, fold: SELECT_FOLD }, CORE_OWNER);
	builtinPaintTools().forEach((tool, index) => railTools.register(entryFor(tool, (index + 1) * 10), CORE_OWNER));
	syncPluginPaintEntries();
	pluginTools.subscribe(syncPluginPaintEntries);
}
