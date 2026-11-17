import { derived, writable, type Readable } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import type { APIResponse, GenerationQueueSnapshot } from '$lib/types/api';
import { EMPTY_ELSEWHERE, selectRunningElsewhere, type ElsewhereList } from './select';

export const ELSEWHERE_MIN_GAP_MS = 1500;
export const ELSEWHERE_IDLE_REFRESH_MS = 15000;
export const ELSEWHERE_BUSY_REFRESH_MS = 4000;
export const ELSEWHERE_FIRST_REFRESH_MS = 2000;

const snapshot = writable<GenerationQueueSnapshot | null>(null);
let lastFetchAt = 0;
let inFlight: Promise<void> | null = null;

export const runningElsewhere: Readable<ElsewhereList> = derived(
	[snapshot, tabsStore],
	([$snapshot, $tabs]) => selectRunningElsewhere($snapshot, $tabs.tabs)
);

export function ingestQueueSnapshot(next: GenerationQueueSnapshot | null, now: number = Date.now()): void {
	if (!next) return;
	lastFetchAt = now;
	snapshot.set(next);
}

export interface ElsewhereApi {
	getGenerationQueue(): Promise<APIResponse<GenerationQueueSnapshot>>;
}

export function refreshRunningElsewhere(
	api: ElsewhereApi,
	options: { now?: () => number; force?: boolean } = {}
): Promise<void> {
	const now = options.now ?? Date.now;
	if (inFlight) return inFlight;
	if (!options.force && now() - lastFetchAt < ELSEWHERE_MIN_GAP_MS) return Promise.resolve();
	inFlight = (async () => {
		try {
			const response = await api.getGenerationQueue();
			if (response.success && response.data) ingestQueueSnapshot(response.data, now());
		} catch {
			return;
		} finally {
			inFlight = null;
		}
	})();
	return inFlight;
}

export interface ElsewhereWatchOptions {
	api: ElsewhereApi;
	isVisible?: () => boolean;
	hasItems?: () => boolean;
	firstDelayMs?: number;
}

export function startElsewhereWatch(options: ElsewhereWatchOptions): () => void {
	const isVisible = options.isVisible ?? (() => typeof document === 'undefined' || document.visibilityState !== 'hidden');
	let current: ElsewhereList = EMPTY_ELSEWHERE;
	const unsubscribe = runningElsewhere.subscribe((value) => (current = value));
	const hasItems = options.hasItems ?? (() => current.total > 0);
	let timer: ReturnType<typeof setTimeout> | null = null;
	let stopped = false;

	const refreshNow = () => {
		if (stopped || !isVisible()) return;
		void refreshRunningElsewhere(options.api);
	};

	const schedule = (delay: number) => {
		if (stopped) return;
		timer = setTimeout(async () => {
			if (isVisible()) await refreshRunningElsewhere(options.api);
			schedule(hasItems() ? ELSEWHERE_BUSY_REFRESH_MS : ELSEWHERE_IDLE_REFRESH_MS);
		}, delay);
	};

	const onVisibility = () => refreshNow();
	if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onVisibility);
	if (typeof window !== 'undefined') window.addEventListener('focus', onVisibility);
	schedule(options.firstDelayMs ?? ELSEWHERE_FIRST_REFRESH_MS);

	return () => {
		stopped = true;
		if (timer) clearTimeout(timer);
		unsubscribe();
		if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVisibility);
		if (typeof window !== 'undefined') window.removeEventListener('focus', onVisibility);
	};
}

export function resetElsewhereForTests(): void {
	snapshot.set(null);
	lastFetchAt = 0;
	inFlight = null;
}
