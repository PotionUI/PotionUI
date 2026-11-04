import type { ToolTabStatus } from '$lib/types/tabs';
import { statusOf } from './listing';
import type { ElixirSummary } from './types';

export interface PickerSummary extends ElixirSummary {
	node_count?: number;
	updated_at?: string | null;
	revision?: number;
	latest_run_id?: string | null;
}

export interface RowStatus {
	label: string;
	tone: string;
	pulse: boolean;
	ring: boolean;
	live: boolean;
}

export const RECENT_LIMIT = 5;
export const MATCH_LIMIT = 8;

export const MANAGE_FILTERS = [
	{ id: 'all', label: 'All' },
	{ id: 'brewing', label: 'Brewing' },
	{ id: 'awaiting', label: 'Needs you' },
	{ id: 'attention', label: 'Failed' }
];

function updatedAt(elixir: PickerSummary): number {
	const value = Date.parse(String(elixir.updated_at ?? elixir.status_at ?? ''));
	return Number.isNaN(value) ? 0 : value;
}

export function byRecent<T extends PickerSummary>(elixirs: readonly T[]): T[] {
	return [...elixirs].sort((a, b) => updatedAt(b) - updatedAt(a));
}

export function matchesQuery(elixir: PickerSummary, query: string): boolean {
	const needle = query.trim().toLowerCase();
	return !needle || String(elixir.name ?? '').toLowerCase().includes(needle);
}

export function pickerRows<T extends PickerSummary>(elixirs: readonly T[], query: string): T[] {
	const limit = query.trim() ? MATCH_LIMIT : RECENT_LIMIT;
	return byRecent(elixirs.filter((elixir) => matchesQuery(elixir, query))).slice(0, limit);
}

export function manageRows<T extends PickerSummary>(elixirs: readonly T[], query: string, filter: string): T[] {
	return byRecent(elixirs.filter((elixir) => matchesQuery(elixir, query) && (filter === 'all' || elixir.status === filter)));
}

export function rowStatus(elixir: PickerSummary, tabStatus: ToolTabStatus | null | undefined = null): RowStatus {
	if (tabStatus) {
		return {
			label: tabStatus.label,
			tone: tabStatus.tone,
			pulse: !!tabStatus.pulse,
			ring: !!tabStatus.ring,
			live: tabStatus.tone === 'signal'
		};
	}
	if (!elixir.node_count && !elixir.status) return { label: 'No steps yet', tone: 'neutral', pulse: false, ring: false, live: false };
	const meta = statusOf(elixir);
	const live = elixir.status === 'brewing' || elixir.status === 'awaiting';
	return { label: meta.label, tone: meta.tone, pulse: live, ring: elixir.status === 'awaiting', live };
}

export function deleteBlocked(elixir: PickerSummary, tabStatus: ToolTabStatus | null | undefined = null): string | null {
	return elixir.status === 'brewing' || elixir.status === 'awaiting' || tabStatus?.tone === 'signal' ? 'Stop its brew first' : null;
}

export function newElixirLabel(query: string): string {
	const name = query.trim();
	return name ? `New elixir “${name}”` : 'New elixir';
}

export function newElixirName(query: string): string {
	return query.trim() || 'Untitled elixir';
}

export function copyName(name: string): string {
	return `${name.trim() || 'Untitled elixir'} copy`;
}

export function stepWord(count: number): string {
	return count === 1 ? 'step' : 'steps';
}
