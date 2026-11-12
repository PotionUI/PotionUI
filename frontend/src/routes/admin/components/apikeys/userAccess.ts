import type { UserApiAccess, UserKeyCounts } from '$lib/services/api/apiKeys';

export type AccessField = keyof UserApiAccess;

export const PEOPLE_PAGE_SIZE = 8;

export function nextAccess(current: UserApiAccess, field: AccessField, value: boolean): UserApiAccess {
	return { ...current, [field]: value };
}

export function mcpUsable(access: UserApiAccess): boolean {
	return access.keys_enabled && access.mcp_enabled;
}

export function accessChangeMessage(name: string, field: AccessField, value: boolean): string {
	if (field === 'keys_enabled') {
		return value
			? `API keys are back on for ${name}. Their keys work again.`
			: `API keys are off for ${name}. Their keys stop working within seconds, open connections included. Nothing is deleted.`;
	}
	return value
		? `MCP is back on for ${name}.`
		: `MCP is off for ${name}. Their MCP clients are cut off within seconds. Their keys keep working over the HTTP API.`;
}

export function accessNote(access: UserApiAccess): { tone: 'info'; text: string } | null {
	if (!access.keys_enabled) return { tone: 'info', text: 'Their keys are refused until you turn this back on. Nothing is deleted.' };
	if (!access.mcp_enabled) return { tone: 'info', text: 'Their MCP clients are refused. Their keys keep working over the HTTP API.' };
	return null;
}

export function keyCountText(total: number): string {
	return `${total} ${total === 1 ? 'key' : 'keys'}`;
}

export function personKeysText(counts: UserKeyCounts | undefined): string {
	if (!counts) return '';
	if (counts.active === counts.total) return keyCountText(counts.total);
	return `${counts.active} of ${keyCountText(counts.total)}`;
}

export function pageOfPeople<T>(people: readonly T[], page: number, size = PEOPLE_PAGE_SIZE): { rows: T[]; page: number; pages: number } {
	const pages = Math.max(1, Math.ceil(people.length / size));
	const current = Math.min(Math.max(1, page), pages);
	return { rows: people.slice((current - 1) * size, current * size), page: current, pages };
}

export function searchPeople<T extends { username?: string; email?: string }>(people: readonly T[], query: string): T[] {
	const q = query.trim().toLowerCase();
	if (!q) return [...people];
	return people.filter((person) => (person.username ?? '').toLowerCase().includes(q) || (person.email ?? '').toLowerCase().includes(q));
}
