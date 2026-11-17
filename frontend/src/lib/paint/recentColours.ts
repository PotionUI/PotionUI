const KEY = 'potionui_paint_recent_colours';
const LIMIT = 8;
export const RECENT_SHOWN = 6;

const HEX = /^#[0-9a-f]{6}$/i;

let memory: string[] = [];
const listeners = new Set<() => void>();

function read(): string[] {
	try {
		const parsed = JSON.parse(localStorage.getItem(KEY) ?? '[]');
		return Array.isArray(parsed) ? parsed.filter((value): value is string => typeof value === 'string' && HEX.test(value)).slice(0, LIMIT) : [];
	} catch {
		return [];
	}
}

let loaded = false;

function ensure(): void {
	if (loaded) return;
	loaded = true;
	memory = read();
}

export function recentColours(): string[] {
	ensure();
	return [...memory];
}

export function rememberColour(hex: string): void {
	if (!HEX.test(hex)) return;
	ensure();
	const next = hex.toLowerCase();
	if (memory[0] === next) return;
	memory = [next, ...memory.filter((value) => value !== next)].slice(0, LIMIT);
	try {
		localStorage.setItem(KEY, JSON.stringify(memory));
	} catch {
		void 0;
	}
	for (const listener of listeners) listener();
}

export function subscribeRecentColours(listener: () => void): () => void {
	listeners.add(listener);
	return () => {
		listeners.delete(listener);
	};
}

export function resetRecentColours(): void {
	loaded = true;
	memory = [];
	for (const listener of listeners) listener();
}
