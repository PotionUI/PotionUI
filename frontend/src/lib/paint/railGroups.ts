import type { RailGroup, RailTool } from '$lib/tool-tabs/frame/types';
import { PAINT_GROUP, PAINT_GROUP_LABEL, SELECT_GROUP, SELECT_GROUP_LABEL, paintIconFor } from './entries';
import type { PaintTool } from './types';

const CANVAS_GROUP = 'canvas';
const ORDER: Record<string, readonly string[]> = {
	[PAINT_GROUP]: ['brush', 'eraser', 'fill', 'pick'],
	[SELECT_GROUP]: ['rect', 'lasso', 'move', 'transform'],
	[CANVAS_GROUP]: ['crop']
};
const FOLD: Record<string, number> = { [CANVAS_GROUP]: 5, [SELECT_GROUP]: 6, [PAINT_GROUP]: 7 };
const LABEL: Record<string, string> = { [PAINT_GROUP]: PAINT_GROUP_LABEL, [SELECT_GROUP]: SELECT_GROUP_LABEL, [CANVAS_GROUP]: 'Canvas' };
const MODAL_ONLY = new Set(['adjust', 'filters']);

function groupOf(tool: PaintTool): string {
	for (const [group, ids] of Object.entries(ORDER)) if (ids.includes(tool.id)) return group;
	return tool.group === SELECT_GROUP ? SELECT_GROUP : tool.group === CANVAS_GROUP ? CANVAS_GROUP : PAINT_GROUP;
}

export function modalRailGroups(tools: readonly PaintTool[]): RailGroup[] {
	const rows = new Map<string, RailTool[]>();
	const wanted = tools.filter((tool) => !MODAL_ONLY.has(tool.id) && typeof tool.label === 'string');
	const place = (group: string) => (tool: PaintTool) => {
		const entry: RailTool = { id: tool.id, label: tool.label, icon: paintIconFor(tool) };
		if (tool.key) entry.keys = tool.key;
		rows.set(group, [...(rows.get(group) ?? []), entry]);
	};
	for (const group of Object.keys(ORDER)) {
		for (const id of ORDER[group]) {
			const tool = wanted.find((candidate) => candidate.id === id);
			if (tool) place(group)(tool);
		}
	}
	const known = new Set(Object.values(ORDER).flat());
	for (const tool of wanted) if (!known.has(tool.id)) place(groupOf(tool))(tool);
	return Object.keys(ORDER)
		.filter((group) => rows.has(group))
		.map((group) => ({ id: group, label: LABEL[group], fold: FOLD[group], tools: rows.get(group)! }));
}
