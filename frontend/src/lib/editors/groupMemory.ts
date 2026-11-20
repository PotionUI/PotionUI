export const GROUP_LAST_STORAGE_KEY = 'potionui_editor_group_last';

type Memory = Partial<Record<string, Record<string, string>>>;

function read(): Memory {
	try {
		const raw = localStorage.getItem(GROUP_LAST_STORAGE_KEY);
		const parsed = raw ? JSON.parse(raw) : null;
		return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
	} catch {
		return {};
	}
}

export function lastInGroups(editor: string): Record<string, string> {
	return { ...(read()[editor] ?? {}) };
}

export function rememberInGroup(editor: string, group: string, entry: string): Record<string, string> {
	const memory = read();
	const mine = { ...(memory[editor] ?? {}) };
	if (mine[group] === entry) return mine;
	mine[group] = entry;
	try {
		localStorage.setItem(GROUP_LAST_STORAGE_KEY, JSON.stringify({ ...memory, [editor]: mine }));
	} catch {
		return mine;
	}
	return mine;
}

export function shownInGroup(entries: readonly string[], active: string | null, last: string | undefined): string {
	if (active && entries.includes(active)) return active;
	if (last && entries.includes(last)) return last;
	return entries[0];
}
