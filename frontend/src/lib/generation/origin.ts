import type { GenerationOrigin, HistoryOriginFilter } from '$lib/types/history';

export type OriginKind = 'mcp' | 'editor' | 'chat';

export interface OriginInfo {
	kind: OriginKind;
	label: string;
	icon: string;
}

export const EDITOR_ORIGIN_LABEL = 'Made in the editor';
export const CHAT_ORIGIN_LABEL = 'Started from chat';
export const MCP_ORIGIN_LABEL = 'Started over MCP';

function keyName(origin: GenerationOrigin): string {
	const current = typeof origin.key_current_name === 'string' ? origin.key_current_name.trim() : '';
	if (current) return current;
	const stored = typeof origin.key_name === 'string' ? origin.key_name.trim() : '';
	return stored;
}

export function describeOrigin(origin: GenerationOrigin | null | undefined): OriginInfo | null {
	if (!origin) return null;
	if (origin.kind === 'mcp') {
		const name = keyName(origin);
		return { kind: 'mcp', label: name ? `${MCP_ORIGIN_LABEL} · ${name}` : MCP_ORIGIN_LABEL, icon: 'plug' };
	}
	if (origin.kind === 'editor') return { kind: 'editor', label: EDITOR_ORIGIN_LABEL, icon: 'sliders' };
	if (origin.kind === 'chat') return { kind: 'chat', label: CHAT_ORIGIN_LABEL, icon: 'chat' };
	return null;
}

export function offersOpenInTab(generation: { origin?: GenerationOrigin | null }): boolean {
	return generation.origin?.kind === 'mcp';
}

export const ORIGIN_FILTER_OPTIONS: ReadonlyArray<{ value: HistoryOriginFilter; label: string }> = [
	{ value: 'mcp', label: MCP_ORIGIN_LABEL },
	{ value: 'editor', label: EDITOR_ORIGIN_LABEL },
	{ value: 'chat', label: CHAT_ORIGIN_LABEL },
	{ value: 'tab', label: 'Started in a tab' }
];

export function originFilterLabel(value: HistoryOriginFilter | undefined | null): string {
	return ORIGIN_FILTER_OPTIONS.find((option) => option.value === value)?.label ?? '';
}
