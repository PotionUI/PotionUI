/**
 * Debounced, identity-owned controller for the generation panel's memory
 * advisory line. Owns the ONLY refresh path so the panel never fires its own
 * `$effect`/reactive re-request loop off the response it just rendered: a
 * caller just calls `refresh(input)` on every input change, and this module
 * decides whether/when that actually reaches the network.
 *
 * Ownership, not just sequencing: `currentKey` is a deterministic identity
 * over the input (preset id, form variant, backend id, form data) that
 * changes the INSTANT a distinct input is seen in `refresh()` itself -
 * before the debounce timer even starts, and independent of whether/when any
 * network request for it is ever issued. Every in-flight request captures
 * the key that was current when IT was scheduled; on completion it only
 * publishes if that key still matches `currentKey`. This is what a plain
 * increasing sequence number gets wrong: a sequence only advances when a
 * request is actually ISSUED, so a request that's still debouncing (never
 * issued yet) can't out-rank an older one that already resolved - here,
 * ownership transfers the moment the newer input is seen, so a stale
 * response can never publish even transiently, no matter how the two
 * interleave:
 *   - A issued (in flight) -> B refresh()'d but still debouncing -> A
 *     resolves: dropped (currentKey is already B's).
 *   - A issued -> B issued (both in flight) -> A resolves: dropped
 *     (currentKey is B's, set the moment B was refresh()'d, before B's own
 *     debounce even elapsed).
 *   - A issued -> input cleared (idle) -> A resolves: dropped, idle stays
 *     idle (currentKey is the idle sentinel).
 */
import { writable } from 'svelte/store';
import { api } from '$lib/services/api';
import type { MemoryPreviewResult } from '$lib/types/api';

export interface MemoryAdvisoryInput {
	presetId: string | null;
	mode?: string;
	formName?: string;
	formData: Record<string, unknown>;
}

export type MemoryAdvisoryStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface MemoryAdvisoryState {
	status: MemoryAdvisoryStatus;
	result: MemoryPreviewResult | null;
	error: string | null;
}

export const IDLE_ADVISORY_STATE: MemoryAdvisoryState = { status: 'idle', result: null, error: null };

const DEBOUNCE_MS = 400;

// Never a real input's key (see `inputKey` - always produced by JSON.stringify
// on an object), so nothing can accidentally own it.
const NOTHING_OWNED_KEY = '';

function inputKey(input: MemoryAdvisoryInput): string {
	return JSON.stringify({
		presetId: input.presetId,
		// `mode` is part of the identity, not just the wire request `run()`
		// sends: switching image -> video on the same preset/form_data (a
		// mode change with no other field touched) must own a NEW key, or
		// the stale image estimate would be treated as still current once
		// video's response arrives.
		mode: input.mode ?? null,
		formName: input.formName ?? null,
		formData: input.formData
	});
}

export function createMemoryAdvisoryController() {
	const store = writable<MemoryAdvisoryState>(IDLE_ADVISORY_STATE);

	let debounceTimer: ReturnType<typeof setTimeout> | null = null;
	// The input identity currently "owned" by the store - see the module
	// docstring for why this, not a request-issuance sequence, is what
	// correctly invalidates a stale in-flight response.
	let currentKey: string = NOTHING_OWNED_KEY;
	let disposed = false;

	function clearTimer(): void {
		if (debounceTimer !== null) {
			clearTimeout(debounceTimer);
			debounceTimer = null;
		}
	}

	async function run(input: MemoryAdvisoryInput, key: string): Promise<void> {
		try {
			const response = await api.previewGenerationMemory({
				preset_id: input.presetId as string,
				mode: input.mode,
				form_name: input.formName,
				form_data: input.formData
			});
			if (disposed || key !== currentKey) return;
			if (response.success && response.data) {
				store.set({ status: 'ready', result: response.data, error: null });
			} else {
				store.set({
					status: 'error',
					result: null,
					error: response.message || response.error || 'Memory estimate unavailable'
				});
			}
		} catch (err) {
			if (disposed || key !== currentKey) return;
			store.set({
				status: 'error',
				result: null,
				error: err instanceof Error ? err.message : 'Memory estimate unavailable'
			});
		}
	}

	return {
		subscribe: store.subscribe,

		/**
		 * Requests a refresh for `input`. A no-op when `input` is identical to
		 * the one this controller already owns (a parent's reactive statement
		 * re-firing with unchanged values never restarts the debounce window
		 * or disturbs an in-flight request for it). A `presetId` of `null`
		 * resets to idle immediately - there is nothing to estimate yet -
		 * without waiting out the debounce, and immediately disowns whatever
		 * was in flight so it can never resurrect a stale result.
		 */
		refresh(input: MemoryAdvisoryInput): void {
			if (disposed) return;
			const key = inputKey(input);
			if (key === currentKey) return;

			currentKey = key; // ownership transfers NOW, before anything is scheduled
			clearTimer();

			if (!input.presetId) {
				store.set(IDLE_ADVISORY_STATE);
				return;
			}

			store.set({ status: 'loading', result: null, error: null });
			debounceTimer = setTimeout(() => {
				debounceTimer = null;
				void run(input, key);
			}, DEBOUNCE_MS);
		},

		dispose(): void {
			disposed = true;
			clearTimer();
		}
	};
}
