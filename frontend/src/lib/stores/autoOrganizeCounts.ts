import { writable } from 'svelte/store';
import { api } from '$lib/services/api/index';

export type AutoOrganizeSubject = 'generations' | 'uploads' | 'models';

export interface AutoOrganizeCount {
	active: number;
	needsAttention: number;
}

export type AutoOrganizeCountsState = Record<AutoOrganizeSubject, AutoOrganizeCount | null>;

const SUBJECT_KEYS: Record<AutoOrganizeSubject, string> = {
	generations: 'generation',
	uploads: 'upload',
	models: 'model'
};

const SUBJECTS = Object.keys(SUBJECT_KEYS) as AutoOrganizeSubject[];

const emptyState = (): AutoOrganizeCountsState => ({
	generations: null,
	uploads: null,
	models: null
});

function asNumber(value: unknown): number | null {
	return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
	return value && typeof value === 'object' ? (value as Record<string, unknown>) : null;
}

export function parseRulesSummary(payload: unknown): AutoOrganizeCountsState {
	const next = emptyState();
	const root = asRecord(payload);
	if (!root) return next;
	const data = asRecord(root.data) ?? root;
	const bySubject = asRecord(data.subjects) ?? data;
	for (const subject of SUBJECTS) {
		const entry = asRecord(bySubject[SUBJECT_KEYS[subject]]) ?? asRecord(bySubject[subject]);
		if (!entry) continue;
		const active = asNumber(entry.active) ?? asNumber(entry.active_count);
		if (active === null) continue;
		next[subject] = {
			active,
			needsAttention: asNumber(entry.needs_attention) ?? asNumber(entry.attention) ?? 0
		};
	}
	return next;
}

function createAutoOrganizeCountsStore() {
	const { subscribe, set } = writable<AutoOrganizeCountsState>(emptyState());
	let inFlight: Promise<void> | null = null;

	async function fetchSummary(): Promise<void> {
		try {
			const response = await api.getClient().get('/api/organize/summary');
			set(parseRulesSummary(response.data));
		} catch {
			set(emptyState());
		}
	}

	return {
		subscribe,
		load(): Promise<void> {
			if (!inFlight) {
				inFlight = fetchSummary().finally(() => {
					inFlight = null;
				});
			}
			return inFlight;
		},
		reset() {
			set(emptyState());
		}
	};
}

export const autoOrganizeCounts = createAutoOrganizeCountsStore();

export function autoOrganizeHref(subject: AutoOrganizeSubject): string {
	return `/auto-organize?subject=${subject}`;
}
