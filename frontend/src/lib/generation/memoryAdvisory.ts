/**
 * Debounced, sequence-guarded controller for the generation panel's memory
 * advisory line. Owns the ONLY refresh path so the panel never fires its own
 * `$effect`/reactive re-request loop off the response it just rendered: a
 * caller just calls `refresh(input)` on every input change, and this module
 * decides whether/when that actually reaches the network.
 *
 * Two independent guards, same discipline as `stores/downloads.ts`'s
 * `listRequestSeq`/`lastAppliedListSeq`:
 *  - a bounded trailing debounce (a burst of `refresh()` calls collapses to
 *    one network request, issued `DEBOUNCE_MS` after the last one), and
 *  - a monotonically increasing request-issuance sequence, so a response for
 *    an input issued earlier can never overwrite the state once a LATER
 *    input has been issued - regardless of which of the two settles first
 *    (the classic out-of-order A-then-B network race).
 */
import { writable } from 'svelte/store';
import { api } from '$lib/services/api';
import type { MemoryPreviewResult } from '$lib/types/api';

export interface MemoryAdvisoryInput {
	presetId: string | null;
	mode?: string;
	formName?: string;
	formData: Record<string, unknown>;
	backendId?: string | null;
}

export type MemoryAdvisoryStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface MemoryAdvisoryState {
	status: MemoryAdvisoryStatus;
	result: MemoryPreviewResult | null;
	error: string | null;
}

export const IDLE_ADVISORY_STATE: MemoryAdvisoryState = { status: 'idle', result: null, error: null };

const DEBOUNCE_MS = 400;

function sameInput(a: MemoryAdvisoryInput | null, b: MemoryAdvisoryInput): boolean {
	return (
		!!a &&
		a.presetId === b.presetId &&
		a.mode === b.mode &&
		a.formName === b.formName &&
		a.backendId === b.backendId &&
		JSON.stringify(a.formData) === JSON.stringify(b.formData)
	);
}

export function createMemoryAdvisoryController() {
	const store = writable<MemoryAdvisoryState>(IDLE_ADVISORY_STATE);

	let debounceTimer: ReturnType<typeof setTimeout> | null = null;
	let seq = 0;
	// Only a response whose request was issued at-or-after this sequence may
	// still write to the store - an older one arriving late is silently
	// dropped, never treated as an error either.
	let lastAppliedSeq = 0;
	let lastIssuedInput: MemoryAdvisoryInput | null = null;
	let disposed = false;

	function clearTimer(): void {
		if (debounceTimer !== null) {
			clearTimeout(debounceTimer);
			debounceTimer = null;
		}
	}

	async function run(input: MemoryAdvisoryInput, requestSeq: number): Promise<void> {
		try {
			const response = await api.previewGenerationMemory({
				preset_id: input.presetId as string,
				mode: input.mode,
				form_name: input.formName,
				form_data: input.formData,
				backend_id: input.backendId ?? undefined
			});
			if (disposed || requestSeq < lastAppliedSeq) return;
			lastAppliedSeq = requestSeq;
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
			if (disposed || requestSeq < lastAppliedSeq) return;
			lastAppliedSeq = requestSeq;
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
		 * the last one actually issued (a parent's reactive statement re-firing
		 * with unchanged values never restarts the debounce window). A
		 * `presetId` of `null` resets to idle immediately - there is nothing to
		 * estimate yet - without waiting out the debounce.
		 */
		refresh(input: MemoryAdvisoryInput): void {
			if (disposed) return;
			if (!input.presetId) {
				clearTimer();
				lastIssuedInput = null;
				store.set(IDLE_ADVISORY_STATE);
				return;
			}
			if (sameInput(lastIssuedInput, input)) return;
			lastIssuedInput = input;

			clearTimer();
			store.set({ status: 'loading', result: null, error: null });
			debounceTimer = setTimeout(() => {
				debounceTimer = null;
				const requestSeq = ++seq;
				void run(input, requestSeq);
			}, DEBOUNCE_MS);
		},

		dispose(): void {
			disposed = true;
			clearTimer();
		}
	};
}
