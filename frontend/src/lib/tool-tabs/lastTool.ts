import { DEFAULT_ACTIVE_TOOL, isActiveToolOf, type EditorKind } from './toolIds';

export const LAST_TOOL_STORAGE_KEY = 'potionui_tool_tab_last';

function read(): Partial<Record<EditorKind, string>> {
	try {
		const raw = localStorage.getItem(LAST_TOOL_STORAGE_KEY);
		const parsed = raw ? JSON.parse(raw) : null;
		return parsed && typeof parsed === 'object' ? parsed : {};
	} catch {
		return {};
	}
}

export function lastToolFor(kind: EditorKind): string {
	const stored = read()[kind];
	return isActiveToolOf(kind, stored) ? stored : DEFAULT_ACTIVE_TOOL[kind];
}

export function rememberTool(tool: string, activeTool: string | null | undefined): void {
	if ((tool !== 'image' && tool !== 'video') || !isActiveToolOf(tool, activeTool)) return;
	if (read()[tool] === activeTool) return;
	try {
		localStorage.setItem(LAST_TOOL_STORAGE_KEY, JSON.stringify({ ...read(), [tool]: activeTool }));
	} catch {
		return;
	}
}
