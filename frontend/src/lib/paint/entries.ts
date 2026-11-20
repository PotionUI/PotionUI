import { editorToolIcons } from '$lib/utils/iconGlyphs';
import { iconPaths, registerIcon } from '$lib/utils/IconLibrary';
import { createBrushTool, createEraserTool } from './tools/brush';
import { createFillTool } from './tools/fill';
import { createMoveTool } from './tools/move';
import { createPickTool } from './tools/pick';
import { createLassoTool, createRectSelectTool } from './tools/select';
import type { PaintTool } from './types';

export const PAINT_ENTRY_PREFIX = 'paint.';
export const PAINT_GROUP = 'paint';
export const SELECT_GROUP = 'select';
export const PAINT_GROUP_LABEL = 'Paint';
export const SELECT_GROUP_LABEL = 'Select and move';

export const PAINT_TOOL_IDS: readonly string[] = ['brush', 'eraser', 'fill', 'pick', 'rect', 'lasso', 'move'];

const SELECT_TOOL_IDS: readonly string[] = ['rect', 'lasso', 'move'];

export function builtinPaintTools(): PaintTool[] {
	const made = [createBrushTool(), createEraserTool(), createFillTool(), createPickTool(), createRectSelectTool(), createLassoTool(), createMoveTool()];
	return PAINT_TOOL_IDS.map((id) => made.find((tool) => tool.id === id)!);
}

export function paintEntryId(toolId: string): string {
	return `${PAINT_ENTRY_PREFIX}${toolId}`;
}

export function isPaintEntry(id: string | null | undefined): id is string {
	return typeof id === 'string' && id.startsWith(PAINT_ENTRY_PREFIX) && id.length > PAINT_ENTRY_PREFIX.length;
}

export function paintToolOfEntry(id: string): string {
	return id.slice(PAINT_ENTRY_PREFIX.length);
}

export function canonicalTool(id: string): string {
	return id === 'paint' ? paintEntryId('brush') : id;
}

export function paintGroupOf(tool: Pick<PaintTool, 'id' | 'group'>): string {
	return SELECT_TOOL_IDS.includes(tool.id) || tool.group === SELECT_GROUP ? SELECT_GROUP : PAINT_GROUP;
}

export function paintIconFor(tool: Pick<PaintTool, 'id' | 'icon'>): string {
	const known = editorToolIcons[tool.id];
	if (known) return known;
	const name = `paint-tool-${tool.id}`;
	return registerIcon(name, tool.icon) || name in iconPaths ? name : editorToolIcons.plugin_fallback;
}

export function paintIconName(toolId: string): string {
	return editorToolIcons[toolId] ?? `paint-tool-${toolId}`;
}
