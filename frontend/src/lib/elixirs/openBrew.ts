import { writable } from 'svelte/store';
import { toasts } from '$lib/stores/toast';
import { getApiErrorMessage } from '$lib/utils/logger';
import { openElixirTab } from './tab';
import type { BlockingRun } from './types';

export interface BrewRequest {
	elixirId: string;
	runId: string;
	number: number;
}

export const brewRequest = writable<BrewRequest | null>(null);

export function requestBrew(blocking: BlockingRun, name?: string | null): void {
	brewRequest.set({ elixirId: blocking.elixir_id, runId: blocking.run_id, number: blocking.number });
	openElixirTab({ id: blocking.elixir_id, name: name ?? blocking.elixir_name });
}

export function blockingRunsOf(error: unknown): BlockingRun[] {
	const list = (error as { response?: { data?: { detail?: { blocking_runs?: unknown } } } })?.response?.data?.detail?.blocking_runs;
	if (!Array.isArray(list)) return [];
	return list.filter(
		(item): item is BlockingRun => !!item && typeof item.elixir_id === 'string' && typeof item.run_id === 'string' && typeof item.number === 'number'
	);
}

export function showDeleteError(error: unknown, fallback?: string): void {
	const message = getApiErrorMessage(error, fallback);
	const first = blockingRunsOf(error)[0];
	if (!first) {
		toasts.error(message);
		return;
	}
	toasts.show('error', message, {
		title: 'Can’t delete yet',
		duration: 0,
		action: { label: 'Open brew', onClick: () => requestBrew(first) }
	});
}
