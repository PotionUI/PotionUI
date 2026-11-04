import type { ElixirSummary, OutputEntry } from './types';

export interface StatusMeta {
	label: string;
	tone: string;
	pulse: boolean;
	group: string;
}

export const STATUS: Record<string, StatusMeta> = {
	brewing: { label: 'Brewing', tone: 'signal', pulse: true, group: 'active' },
	awaiting: { label: 'Waiting for your choice', tone: 'signal', pulse: false, group: 'active' },
	attention: { label: 'Needs attention', tone: 'danger', pulse: false, group: 'attention' },
	done: { label: 'Done', tone: 'success', pulse: false, group: 'done' },
	stopped: { label: 'Stopped', tone: 'warning', pulse: false, group: 'stopped' },
	not_brewed: { label: 'Not brewed yet', tone: 'neutral', pulse: false, group: 'not_brewed' }
};

export const FILTERS = [
	{ id: 'all', label: 'All', icon: 'list' },
	{ id: 'active', label: 'Active', icon: 'play' },
	{ id: 'attention', label: 'Needs attention', icon: 'warning' },
	{ id: 'done', label: 'Done', icon: 'check' },
	{ id: 'not_brewed', label: 'Not brewed', icon: 'clock' }
];

export const STRIP_SLOTS = 5;

const TIMED = new Set(['done', 'stopped']);

type Summary = Partial<ElixirSummary> | null | undefined;

export interface PrimaryAction {
	id: string;
	label: string;
	variant: string;
	icon: string;
}

export function statusOf(elixir: Summary): StatusMeta {
	return STATUS[elixir?.status ?? ''] ?? STATUS.not_brewed;
}

export function statusKey(elixir: Summary): string {
	return elixir?.status && STATUS[elixir.status] ? elixir.status : 'not_brewed';
}

export function statusText(elixir: Summary, ago: (value: string) => string): string {
	const meta = statusOf(elixir);
	if (!TIMED.has(statusKey(elixir)) || !elixir?.status_at) return meta.label;
	const when = ago(elixir.status_at);
	return when ? `${meta.label} ${when}` : meta.label;
}

export function matchesFilter(elixir: Summary, filter: string): boolean {
	if (filter === 'all') return true;
	return statusOf(elixir).group === filter;
}

export function filterElixirs<T extends Partial<ElixirSummary>>(elixirs: T[], { query = '', filter = 'all' }: { query?: string; filter?: string } = {}): T[] {
	const needle = query.trim().toLowerCase();
	return elixirs.filter((elixir) => matchesFilter(elixir, filter) && (!needle || String(elixir.name).toLowerCase().includes(needle)));
}

export function filterCounts(elixirs: Array<Partial<ElixirSummary>>, query = ''): Record<string, number> {
	const counts: Record<string, number> = Object.fromEntries(FILTERS.map((entry) => [entry.id, 0]));
	for (const elixir of filterElixirs(elixirs, { query })) {
		counts.all += 1;
		const group = statusOf(elixir).group;
		if (group in counts) counts[group] += 1;
	}
	return counts;
}

export function primaryAction(elixir: Summary): PrimaryAction {
	switch (statusKey(elixir)) {
		case 'brewing':
			return { id: 'stop', label: 'Stop', variant: 'danger', icon: 'close' };
		case 'awaiting':
			return { id: 'choose', label: 'Choose outputs', variant: 'primary', icon: 'check' };
		case 'not_brewed':
			return { id: 'brew', label: 'Brew', variant: 'primary', icon: 'play' };
		default:
			return { id: 'brew', label: 'Brew again', variant: 'primary', icon: 'play' };
	}
}

export function stripLayout(elixir: Summary, slots: number = STRIP_SLOTS): { tiles: OutputEntry[]; more: number; placeholders: number } {
	const outputs = elixir?.latest_outputs ?? [];
	const total = Math.max(elixir?.output_count ?? 0, outputs.length);
	const overflow = total > slots;
	const shown = outputs.slice(0, overflow ? slots - 1 : slots);
	const more = overflow ? total - shown.length : 0;
	const filled = shown.length + (more ? 1 : 0);
	return { tiles: shown, more, placeholders: Math.max(0, slots - filled) };
}

export function runModeLabel(runMode: string | null | undefined): string {
	return runMode === 'automatic' ? 'Runs all branches' : 'Choose outputs';
}

export const POLL_MS = 5000;
export const POLL_IDLE_MS = 30000;

export function pollDelay(elixirs: Array<Partial<ElixirSummary>> | null | undefined): number | null {
	const list = elixirs ?? [];
	if (list.some((elixir) => elixir.status === 'brewing')) return POLL_MS;
	if (list.some((elixir) => elixir.status === 'awaiting' || elixir.status === 'attention')) return POLL_IDLE_MS;
	return null;
}

export function brewKey(): string {
	const random = typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
	return `list-brew-${random}`;
}

export function mergeSummary<T extends { id: string }>(elixirs: T[], id: string, changes: Partial<T>): T[] {
	return elixirs.map((item) => (item.id === id ? { ...item, ...changes } : item));
}
