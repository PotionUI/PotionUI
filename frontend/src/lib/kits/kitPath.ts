export const KITS_SOURCE = 'kits';

export function formatKitPath(handle: string, item?: string | null): string {
	return item ? `@kits.${handle}.${item}` : `@kits.${handle}`;
}

export function kitPathLabel(handle: string, item?: string | null): string {
	return item ? `${handle}.${item}` : handle;
}

export interface ParsedKitPath {
	handle: string;
	item: string | null;
}

export function parseKitPath(text: string): ParsedKitPath | null {
	const match = /^@kits\.([a-z][a-z0-9_]*)(?:\.([a-z][a-z0-9_]*))?$/.exec(text.trim());
	if (!match) return null;
	return { handle: match[1], item: match[2] ?? null };
}

export interface KitQuery {
	handle: string | null;
	item: string | null;
	drilled: boolean;
}

export function parseKitQuery(query: string): KitQuery | null {
	const lower = query.toLowerCase();
	if (!lower.startsWith('kits.') && lower !== 'kits') return null;
	const rest = lower.slice('kits.'.length);
	if (lower === 'kits') return { handle: null, item: null, drilled: false };
	const dot = rest.indexOf('.');
	if (dot === -1) return { handle: rest, item: null, drilled: false };
	return { handle: rest.slice(0, dot), item: rest.slice(dot + 1), drilled: true };
}
