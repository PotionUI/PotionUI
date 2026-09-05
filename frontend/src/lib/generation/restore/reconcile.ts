// Reload/reconnect reconciliation for a tab's in-flight generations
// (routes/generate/+page.svelte's `restoreGenerations`, which supersedes the
// old separate `restoreActiveGenerations`/`restoreQueuedGenerations`).
// Persisted state can name a generation the frontend must re-confirm against
// the server rather than trust blindly: `activeGenerationId` (the tab's
// shared display), `directorRuns` (a Video Director shot, keyed by shot id,
// `queued`/`generating`) and `directorRunLinks` (generationId -> shot id(s)
// -- a shot can be tracked ONLY here once its `directorRuns` entry has been
// overwritten by a resubmission). `generation.queue` (unpersisted, but
// possibly already populated this session) and the caller's own
// `extraCandidateIds` (ids the live backend queue snapshot reports for this
// tab, which may not appear in ANY persisted field yet) round out the set.
// None subsumes another, so every id from every source is looked up once.
//
// Routing matters here as much as state: `findTabByGenerationId`
// ($lib/stores/generation.ts) only ever finds a tab through
// `currentGeneration` or an entry in `generation.queue` -- it has no idea
// `directorRunLinks` exists. A Director shot's generationId that never
// becomes `activeGenerationId` therefore needs a `generation.queue` entry of
// its own or every subsequent WebSocket message for it dispatches nowhere
// ("Tab not found"), and the shot's progress/poster never updates again.
// `withRoutingEntry` below is what gives a confirmed pending/running id that
// entry regardless of whether it also owns the shared display.
//
// Subscription dedup is a SEPARATE concern from routing and must never be
// keyed off `generation.queue` membership: that queue is TAB state (the
// tabs store is module-scope and survives an SPA navigate away and back),
// while the WebSocket listener lives on a `WebSocketService` INSTANCE that
// does NOT survive one (`routes/generate/+page.svelte`'s `onMount` builds a
// fresh one on every mount). A queue-membership check wrongly reads "already
// routed on some past socket" as "already subscribed on THIS one", so a
// generation that outlives a remount would never get a listener on the new
// socket at all.
//
// Nor is reconcile.ts's own subscribe request the only one that can happen
// for a given id: the page also subscribes directly at fresh submission
// (startGeneration and the Video Director shot submission). A generation
// that completes its FIRST reconnect reconciliation pass while still
// running would otherwise be asked to subscribe a second time even though
// submission already did. `./subscriptions.ts`'s `ensureSubscribed` is the
// ONE gate both call sites go through, keyed on `options.subscriptionOwner`
// -- a stable value identifying the CURRENT live listener target, in
// practice the page's own `WebSocketService` instance -- so whichever call
// site asks FIRST for a given (owner, generationId) pair is the only one
// that ever triggers a real `ws.subscribe`. See that file for the full
// contract, including `clearSubscriptionOwner` (an `onDestroy` backstop) and
// `releaseSubscription` (per-id bookkeeping released on terminal
// unsubscribe, wired through the page's unified `unsubscribeGeneration`).
//
// No-resurrection: a "keep" outcome (server says pending/running) is only
// ever applied if the id is STILL claimed by the tab at apply time --
// `isRetiredKeep` compares what was true when THIS pass started against
// what is true right now, re-read from the store. A generation this pass
// captured as "in flight" can legitimately finish via its own live
// `generation_complete`/`generation_error` WHILE this pass's HTTP lookup is
// still in flight; when the belated "still running" response lands, nothing
// on the tab claims that id any more and re-adding its routing entry would
// resurrect a run that is, in fact, done.
//
// This module never adopts an orphaned tab's queued/running generation as
// its live display itself -- that is `$lib/generation/messages/ownership.ts`
// (`resolveOwnership`/`beginGenerationOwnership`)'s job, triggered by the
// live event that follows once routing/subscription is restored here.
// Duplicating that here would fight it over which one performs the cold
// start.
//
// Every director-run mutation goes through the SAME identity-guarded
// reducers `$lib/generation/messages/directorRuns.ts` uses for live
// WebSocket messages (`withDirectorRunTerminal`/`withDirectorRunPoster`
// require `existing.generationId === generationId`), so a belated restore
// response for a shot that has since been resubmitted under a new
// generation id is a silent no-op here exactly as it is there. The
// shared-display patch (`activeGenerationId`) gets the same treatment by
// hand: every apply re-reads the tab from the store and writes only if it
// still points at the id this lookup was for.
//
// A component teardown (SPA navigation away from /generate, or a fast
// reconnect flap) can leave a lookup or its retry awaiting a response after
// the page that started it is gone. `options.signal` (an `AbortSignal` the
// caller retires on `onDestroy`, before disconnecting its WebSocket) is
// checked after every `await` in this module, including immediately before
// `onSubscribe` -- a late response must never subscribe on, or write into,
// a tab/store that has already been torn down.
import { get } from 'svelte/store';
import type { Readable } from 'svelte/store';
import type { Tab, GenerationState, QueuedGeneration } from '$lib/types/tabs';
import type { APIResponse, GenerationStatus } from '$lib/types/api';
import { mapGenerationFiles, type RestoredGenerationData } from '$lib/utils/generationOrchestrator';
import { leadIndex } from '$lib/generation/leadFile';
import {
	directorShotIdsFor,
	withDirectorRunTerminal,
	withDirectorRunPoster,
	withoutDirectorRunLink
} from '$lib/generation/messages/directorRuns';
import { withoutQueueEntry } from '$lib/generation/messages/ownership';
import { retireGeneration } from '$lib/generation/messages/generationOutputs';
import { ensureSubscribed } from './subscriptions';

/** Minimal surface `reconcileTabGenerations` needs from `tabsStore` -- the
 *  real store (`$lib/stores/tabs`) satisfies this as-is. */
export interface ReconcileTabsStore extends Readable<{ tabs: Tab[]; activeTabId: string }> {
	updateTab(tabId: string, updates: Partial<Tab>): void;
}

export interface ReconcileApi {
	getGenerationStatus(generationId: string): Promise<APIResponse<GenerationStatus>>;
	getGenerationById(
		generationId: string,
		includeTags?: boolean,
		includeFiles?: boolean
	): Promise<APIResponse<{ files?: unknown[] }>>;
}

export interface ReconcileOptions {
	/** Max concurrent status lookups in flight at once (default 4). */
	chunkSize?: number;
	/** Delay before the single retry on a transient failure, ms (default 1500). */
	retryDelayMs?: number;
	/** Called once per generation id this reload should keep listening to
	 *  (still pending/running) -- the caller re-subscribes on its live
	 *  WebSocket connection. Never called twice for the same id against the
	 *  SAME `subscriptionOwner` (see the file header), and never called for
	 *  an id this tab has since retired. */
	onSubscribe?: (generationId: string) => void;
	/** A stable value identifying the CURRENT live listener target -- in
	 *  production, the page's own `WebSocketService` instance. Subscription
	 *  dedup is scoped to this value (see the file header on why it must be
	 *  the socket, never tab/routing state): omitted, every "keep" outcome
	 *  calls `onSubscribe` with no dedup at all. */
	subscriptionOwner?: unknown;
	/** Called for every generation id this pass resolves terminal (completed/
	 *  failed/cancelled/missing) -- the caller's live WebSocket unsubscribe,
	 *  same contract as `dispatchGenerationMessage`'s `DispatchDeps.unsubscribe`.
	 *  A generation resolved here can have been subscribed in an EARLIER
	 *  session/pass (its live `generation_complete`/`generation_error` never
	 *  arrived before disconnect) without this pass ever calling `onSubscribe`
	 *  for it, so the unsubscribe still has to happen even though no matching
	 *  subscribe happens in THIS pass. Defaults to a no-op. */
	unsubscribe?: (generationId: string) => void;
	/** Ids the caller already knows are this tab's from a source OUTSIDE
	 *  persisted tab state (e.g. the live `/api/generations/queue` snapshot,
	 *  scoped to this tab's `tab_id`) -- folded into the same reconciliation
	 *  pass instead of being trusted/merged separately, so a stale or
	 *  delayed snapshot can never re-add an id this pass (or a live event
	 *  racing it) has already resolved as terminal: every id, wherever it
	 *  came from, is re-confirmed against `getGenerationStatus` here. */
	extraCandidateIds?: string[];
	/** Retired (aborted) by the caller on component teardown, before it
	 *  disconnects its WebSocket -- see the file header. Checked after every
	 *  await; a pass already retired applies nothing further and subscribes
	 *  nothing further. */
	signal?: AbortSignal;
	now?: () => number;
}

const DEFAULT_CHUNK_SIZE = 4;
const DEFAULT_RETRY_DELAY_MS = 1500;

/** `GenerationStatus.status`'s actual value set -- anything else (absent,
 *  misspelled, a future value this build doesn't know yet) must never be
 *  read as authoritative in either direction. */
const KNOWN_GENERATION_STATUSES = new Set(['pending', 'running', 'completed', 'failed', 'cancelled']);

function hasRecognizedStatus(data: GenerationStatus | null | undefined): data is GenerationStatus {
	return !!data && typeof data.status === 'string' && KNOWN_GENERATION_STATUSES.has(data.status);
}

function delay(ms: number): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

function chunk<T>(items: T[], size: number): T[][] {
	const out: T[][] = [];
	for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
	return out;
}

/** Same parsing `restoreActiveGenerations` used inline -- accepts either a
 *  unix-seconds or epoch-ms timestamp, or an ISO string. */
export function generationTimestampMs(value?: string | number | null): number | null {
	if (value === undefined || value === null) return null;
	const numeric = Number(value);
	if (Number.isFinite(numeric)) return numeric < 1_000_000_000_000 ? numeric * 1000 : numeric;
	const parsed = Date.parse(String(value));
	return Number.isNaN(parsed) ? null : parsed;
}

/** Every generation id this tab has persisted as still in flight -- the set
 *  a reload/reconnect must re-confirm against the server rather than trust
 *  blindly. See the file header for why none of the sources subsumes
 *  another. */
export function collectInFlightGenerationIds(
	tab: Pick<Tab, 'activeGenerationId' | 'directorRuns' | 'directorRunLinks'> & {
		queue?: QueuedGeneration[];
		extraCandidateIds?: string[];
	}
): string[] {
	const ids = new Set<string>();
	if (tab.activeGenerationId) ids.add(tab.activeGenerationId);
	for (const run of Object.values(tab.directorRuns || {})) {
		if (run.status === 'queued' || run.status === 'generating') ids.add(run.generationId);
	}
	for (const linkedId of Object.keys(tab.directorRunLinks || {})) ids.add(linkedId);
	for (const queued of tab.queue || []) ids.add(queued.generation_id);
	for (const extra of tab.extraCandidateIds || []) ids.add(extra);
	return [...ids];
}

/** A confirmed 404 (`generation_not_found`, raised by both
 *  `GenerationController.get_generation_status` and `get_generation_by_id`)
 *  proves the generation is gone. Everything else -- no response at all
 *  (network down/timeout), a 5xx, or an unexpected response shape -- is a
 *  transient failure that must never be read as "missing": doing so on a
 *  disconnect/restart during reload would silently destroy the only
 *  reference back to a generation that is, in fact, still running. */
export function isConfirmedMissing(err: unknown, response?: APIResponse<unknown> | null): boolean {
	const status = (err as { response?: { status?: number } } | null | undefined)?.response?.status;
	if (status === 404) return true;
	if (response && response.success === false && response.error === 'generation_not_found') return true;
	return false;
}

function leadOutputInfo(outputs: RestoredGenerationData | null): {
	url: string | null;
	fileType: 'image' | 'video' | 'audio' | 'mesh' | null;
} {
	if (!outputs) return { url: null, fileType: null };
	const { images, videos, audios, meshes } = outputs;
	const all = [...images, ...videos, ...audios, ...meshes];
	if (all.length === 0) return { url: null, fileType: null };
	const idx = leadIndex(all);
	const item = all[idx] as { url?: string; originalUrl?: string } | undefined;
	let fileType: 'image' | 'video' | 'audio' | 'mesh' = 'image';
	if (idx >= images.length + videos.length + audios.length) fileType = 'mesh';
	else if (idx >= images.length + videos.length) fileType = 'audio';
	else if (idx >= images.length) fileType = 'video';
	return { url: item?.originalUrl ?? item?.url ?? null, fileType };
}

interface OutputsFetchAttempt {
	/** `null` ONLY when the fetch itself failed (thrown, or an
	 *  unsuccessful/malformed envelope) -- a genuinely empty file list is a
	 *  successful, conclusive answer (`RestoredGenerationData` with empty
	 *  arrays), never `null`. Callers rely on that distinction to tell
	 *  "still don't know" from "confirmed nothing". */
	outputs: RestoredGenerationData | null;
	/** Set when `outputs` is `null` -- lets a caller classify the failure
	 *  (confirmed-missing vs. transient) without a second network call. */
	thrown: unknown;
	response: APIResponse<{ files?: unknown[] }> | null;
}

/** One attempt at a generation's history/output -- see `OutputsFetchAttempt`. */
async function fetchOutputsOnce(api: ReconcileApi, generationId: string): Promise<OutputsFetchAttempt> {
	let response: APIResponse<{ files?: unknown[] }> | null = null;
	let thrown: unknown = null;
	try {
		response = await api.getGenerationById(generationId, false, true);
	} catch (err) {
		thrown = err;
	}
	if (thrown === null && response?.success && response.data) {
		return {
			outputs: mapGenerationFiles((response.data.files as unknown[]) || [], generationId),
			thrown: null,
			response
		};
	}
	return { outputs: null, thrown, response };
}

/** The status lookup already confirmed 'completed' -- a history-fetch
 *  failure here is never read as "maybe not done after all", only as
 *  "no poster yet". One retry (same transient-failure assumption as the
 *  status lookup) before giving up FOR THIS PASS -- `applyTerminal` still
 *  resolves the run as done; see `pendingPosterRecoveries` for what happens
 *  to the poster after both attempts fail. */
async function fetchOutputs(
	api: ReconcileApi,
	generationId: string,
	retryDelayMs: number,
	signal: AbortSignal | undefined,
	isRetry = false
): Promise<RestoredGenerationData | null> {
	const attempt = await fetchOutputsOnce(api, generationId);
	if (attempt.outputs || signal?.aborted) return attempt.outputs;
	if (isRetry) return null;
	await delay(retryDelayMs);
	if (signal?.aborted) return null;
	return fetchOutputs(api, generationId, retryDelayMs, signal, true);
}

/** Mirrors the shared-display patch `restoreActiveGenerations` used to build
 *  inline for a generation that finished while this tab was disconnected. */
function buildActiveCompletionPatch(
	generationId: string,
	outputs: RestoredGenerationData | null
): Partial<GenerationState> {
	if (!outputs) {
		return {
			isGenerating: false,
			currentGeneration: { status: 'completed', id: generationId, generation_id: generationId }
		};
	}
	const { images, videos, audios, meshes } = outputs;
	const all = [...images, ...videos, ...audios, ...meshes];
	const workbenchIndex = leadIndex(all);
	const { url: leadUrl, fileType } = leadOutputInfo(outputs);
	const leadAudio = fileType === 'audio' ? audios[workbenchIndex - images.length - videos.length] : null;
	return {
		isGenerating: false,
		currentGeneration:
			leadUrl || leadAudio
				? {
						status: 'completed',
						id: generationId,
						generation_id: generationId,
						current_image: fileType === 'image' ? leadUrl : null,
						current_video: fileType === 'video' ? leadUrl : null,
						current_audio: fileType === 'audio' ? leadAudio : null,
						current_mesh: fileType === 'mesh' ? leadUrl : null,
						file_type: fileType
					}
				: { status: 'completed', id: generationId, generation_id: generationId },
		batchImages: images,
		batchVideos: videos,
		batchAudios: audios,
		batchMeshes: meshes,
		workbenchIndex,
		workbenchTotal: all.length
	};
}

function buildActiveFailurePatch(
	generationId: string,
	backendStatus: 'failed' | 'cancelled',
	message: string | null
): Partial<GenerationState> {
	return {
		isGenerating: false,
		currentGeneration:
			backendStatus === 'failed'
				? {
						status: 'failed',
						id: generationId,
						generation_id: generationId,
						message: message || 'Generation failed'
					}
				: null
	};
}

/** Adds (or refreshes the status word of) a `generation.queue` entry for a
 *  confirmed pending/running id -- the ONLY thing that makes
 *  `findTabByGenerationId` route this tab's future WebSocket messages for
 *  it, whether or not it also owns the shared display. A no-op re-render
 *  when the entry already matches. */
function withRoutingEntry(
	queue: QueuedGeneration[] | undefined,
	generationId: string,
	backendStatus: 'pending' | 'running'
): QueuedGeneration[] {
	const existing = queue || [];
	const current = existing.find((q) => q.generation_id === generationId);
	if (current) {
		return current.status === backendStatus
			? existing
			: existing.map((q) => (q.generation_id === generationId ? { ...q, status: backendStatus } : q));
	}
	return [...existing, { generation_id: generationId, queue_position: null, status: backendStatus }];
}

/** Snapshot of what this pass considered "claimed" for `generationId` when
 *  it started -- the baseline `isRetiredKeep` compares the CURRENT tab
 *  against, to tell "legitimately new" (an extraCandidateId this tab never
 *  knew about) from "was claimed, and something has since let go of it". */
interface SeedTracking {
	activeGenerationId: string | null;
	directorLinkIds: ReadonlySet<string>;
	queueIds: ReadonlySet<string>;
}

function wasTrackedAtStart(seed: SeedTracking, generationId: string): boolean {
	return (
		seed.activeGenerationId === generationId ||
		seed.directorLinkIds.has(generationId) ||
		seed.queueIds.has(generationId)
	);
}

/** True when NOTHING on the tab currently claims `generationId` any more --
 *  not the active display, not a Director link, not a queue entry. */
function hasLiveClaim(tab: Tab, generationId: string): boolean {
	if (tab.activeGenerationId === generationId) return true;
	if (directorShotIdsFor(tab, generationId) !== null) return true;
	if ((tab.generation.queue || []).some((q) => q.generation_id === generationId)) return true;
	return false;
}

/** No-resurrection guard for a "keep" (pending/running) outcome: an id this
 *  pass never considered tracked (a brand-new extraCandidateId) is never
 *  "retired" -- there is nothing to resurrect. An id that WAS tracked at the
 *  start of this pass but, by apply time, nothing on the tab claims any
 *  more has been resolved by something else (typically its own live
 *  `generation_complete`/`generation_error` arriving while this pass's HTTP
 *  lookup was still in flight) -- re-adding its routing/display here would
 *  resurrect a run that is, in fact, done. */
function isRetiredKeep(tab: Tab, generationId: string, seed: SeedTracking): boolean {
	if (!wasTrackedAtStart(seed, generationId)) return false;
	return !hasLiveClaim(tab, generationId);
}

interface PendingPosterRecovery {
	tabId: string;
	shotIds: string[];
	attempts: number;
}

/** A Director run that reached 'done' but whose history/poster fetch
 *  exhausted `fetchOutputs`'s in-pass retry -- not forgotten outright. The
 *  NEXT `reconcileTabGenerations` pass for the same tab retries just this
 *  one history fetch (never a full status re-poll: the generation's
 *  completion is already conclusively known) and patches the poster in
 *  through the same identity-guarded `withDirectorRunPoster` reducer live
 *  `gallery_update`s use, so a shot resubmitted in the meantime is
 *  untouched. Module-level (survives across passes/reconnects the way
 *  `generationOutputs.ts`'s cache does), bounded on both axes: at most
 *  `MAX_PENDING_POSTER_RECOVERIES` entries (oldest evicted first) and at
 *  most `MAX_POSTER_RECOVERY_ATTEMPTS` retries per id before giving up for
 *  good -- a generation whose files are gone must not be retried forever. */
const pendingPosterRecoveries = new Map<string, PendingPosterRecovery>();
const MAX_PENDING_POSTER_RECOVERIES = 20;
const MAX_POSTER_RECOVERY_ATTEMPTS = 5;

function rememberPendingPosterRecovery(tabId: string, generationId: string, shotIds: string[]): void {
	if (!pendingPosterRecoveries.has(generationId) && pendingPosterRecoveries.size >= MAX_PENDING_POSTER_RECOVERIES) {
		const oldestKey = pendingPosterRecoveries.keys().next().value;
		if (oldestKey !== undefined) pendingPosterRecoveries.delete(oldestKey);
	}
	pendingPosterRecoveries.set(generationId, { tabId, shotIds, attempts: 0 });
}

/** Test-only: production never needs this (a real page load resets the
 *  module fresh, and every entry self-prunes on success or confirmed-404),
 *  but a test suite reusing generation ids across unrelated cases needs a
 *  clean slate. */
export function resetPendingPosterRecoveriesForTests(): void {
	pendingPosterRecoveries.clear();
}

/**
 * Reconciles every generation id `tabId` has in flight -- persisted
 * (`activeGenerationId`, non-terminal `directorRuns`, `directorRunLinks`
 * keys, already-known `generation.queue` entries) plus any
 * `options.extraCandidateIds` the caller discovered from the live backend
 * queue snapshot -- against the server's authoritative status, PLUS
 * retrying any Director run still owed a poster from a previous pass (see
 * `pendingPosterRecoveries`). Applies terminal results, restores routing +
 * re-subscribes (at most once per `subscriptionOwner` per id) to ones still
 * running, and drops
 * a "keep" outcome for an id nothing on the tab claims any more. Safe to
 * call on every reconnect (not just once per mount): a no-op tab (nothing
 * tracked, no candidates, no pending poster) resolves immediately without
 * any network calls.
 */
export async function reconcileTabGenerations(
	tabId: string,
	api: ReconcileApi,
	tabsStore: ReconcileTabsStore,
	options: ReconcileOptions = {}
): Promise<void> {
	const chunkSize = options.chunkSize ?? DEFAULT_CHUNK_SIZE;
	const retryDelayMs = options.retryDelayMs ?? DEFAULT_RETRY_DELAY_MS;
	const now = options.now ?? Date.now;
	const onSubscribe = options.onSubscribe;
	const unsubscribe = options.unsubscribe ?? (() => {});
	const subscriptionOwner = options.subscriptionOwner;
	const signal = options.signal;

	const readTab = (): Tab | undefined => get(tabsStore).tabs.find((t) => t.id === tabId);

	if (signal?.aborted) return;
	const seedTab = readTab();
	if (!seedTab) return;

	async function recoverPendingPosters(): Promise<void> {
		const entries = [...pendingPosterRecoveries.entries()].filter(([, v]) => v.tabId === tabId);
		for (const [generationId, entry] of entries) {
			if (signal?.aborted) return;
			entry.attempts += 1;
			const attempt = await fetchOutputsOnce(api, generationId);
			if (signal?.aborted) return;
			if (attempt.outputs) {
				const posterUrl = leadOutputInfo(attempt.outputs).url;
				if (posterUrl) {
					const tab = readTab();
					if (tab) {
						tabsStore.updateTab(tabId, {
							directorRuns: withDirectorRunPoster(tab, entry.shotIds, posterUrl, generationId)
						});
					}
				}
				pendingPosterRecoveries.delete(generationId);
				continue;
			}
			// The fetch itself failed again. A confirmed-404 (the generation's
			// history record is genuinely gone) or an exhausted attempt budget
			// both mean giving up for good; anything else waits for the pass
			// after this one.
			const confirmedMissing = isConfirmedMissing(attempt.thrown, attempt.response);
			if (confirmedMissing || entry.attempts >= MAX_POSTER_RECOVERY_ATTEMPTS) {
				pendingPosterRecoveries.delete(generationId);
			}
		}
	}

	await recoverPendingPosters();
	if (signal?.aborted) return;

	const seed: SeedTracking = {
		activeGenerationId: seedTab.activeGenerationId ?? null,
		directorLinkIds: new Set(Object.keys(seedTab.directorRunLinks || {})),
		queueIds: new Set((seedTab.generation.queue || []).map((q) => q.generation_id))
	};
	const ids = collectInFlightGenerationIds({
		...seedTab,
		queue: seedTab.generation.queue,
		extraCandidateIds: options.extraCandidateIds
	});
	if (ids.length === 0) return;

	function applyMissing(generationId: string): void {
		const tab = readTab();
		if (!tab) return;
		const patch: Partial<Tab> = {
			generation: { ...tab.generation, queue: withoutQueueEntry(tab.generation.queue, generationId) }
		};
		if (tab.activeGenerationId === generationId) {
			patch.activeGenerationId = null;
			patch.generation = {
				...patch.generation!,
				...buildActiveFailurePatch(generationId, 'failed', 'Generation not found')
			};
		}
		const shotIds = directorShotIdsFor(tab, generationId);
		if (shotIds) {
			patch.directorRuns = withDirectorRunTerminal(tab, shotIds, 'failed', null, now(), generationId);
			patch.directorRunLinks = withoutDirectorRunLink(tab, generationId);
		}
		tabsStore.updateTab(tabId, patch);
		retireGeneration(generationId, unsubscribe);
	}

	function applyKeep(generationId: string, status: GenerationStatus): void {
		const tab = readTab();
		if (!tab) return;
		const isOwner = tab.activeGenerationId === generationId;
		tabsStore.updateTab(tabId, {
			generation: {
				...tab.generation,
				queue: withRoutingEntry(tab.generation.queue, generationId, status.status as 'pending' | 'running'),
				...(isOwner
					? {
							isGenerating: true,
							startedAt:
								generationTimestampMs(status.started_at ?? status.created_at) ??
								tab.generation.startedAt ??
								now(),
							currentGeneration: { ...status, id: generationId, generation_id: generationId }
						}
					: {})
			}
		});
	}

	function applyTerminal(
		generationId: string,
		backendStatus: 'completed' | 'failed' | 'cancelled',
		outputs: RestoredGenerationData | null,
		message: string | null
	): void {
		const tab = readTab();
		if (!tab) return;
		const patch: Partial<Tab> = {
			generation: { ...tab.generation, queue: withoutQueueEntry(tab.generation.queue, generationId) }
		};
		if (tab.activeGenerationId === generationId) {
			patch.activeGenerationId = null;
			patch.generation = {
				...patch.generation!,
				...(backendStatus === 'completed'
					? buildActiveCompletionPatch(generationId, outputs)
					: buildActiveFailurePatch(generationId, backendStatus, message))
			};
		}
		const shotIds = directorShotIdsFor(tab, generationId);
		if (shotIds) {
			const posterUrl = backendStatus === 'completed' ? leadOutputInfo(outputs).url : null;
			patch.directorRuns = withDirectorRunTerminal(
				tab,
				shotIds,
				backendStatus === 'completed' ? 'done' : 'failed',
				posterUrl,
				now(),
				generationId
			);
			patch.directorRunLinks = withoutDirectorRunLink(tab, generationId);
			if (backendStatus === 'completed' && outputs === null) {
				rememberPendingPosterRecovery(tabId, generationId, shotIds);
			}
		}
		tabsStore.updateTab(tabId, patch);
		retireGeneration(generationId, unsubscribe);
	}

	async function reconcileOne(generationId: string, isRetry: boolean): Promise<void> {
		let statusResponse: APIResponse<GenerationStatus> | null = null;
		let thrown: unknown = null;
		try {
			statusResponse = await api.getGenerationStatus(generationId);
		} catch (err) {
			thrown = err;
		}
		if (signal?.aborted) return;

		const status = statusResponse?.data;
		const malformed =
			thrown === null && !!statusResponse && statusResponse.success && !hasRecognizedStatus(status);

		if (thrown !== null || !statusResponse || !statusResponse.success || !status || malformed) {
			if (!malformed && isConfirmedMissing(thrown, statusResponse)) {
				applyMissing(generationId);
				return;
			}
			// Transient (network/5xx) or an unrecognized/malformed response
			// body -- neither proves the generation is gone. Leave
			// `activeGenerationId`/the run/its routing untouched and try once
			// more after a delay.
			if (!isRetry) {
				await delay(retryDelayMs);
				if (signal?.aborted) return;
				await reconcileOne(generationId, true);
			}
			return;
		}

		if (status.status === 'pending' || status.status === 'running') {
			const tab = readTab();
			if (!tab || isRetiredKeep(tab, generationId, seed)) return;
			applyKeep(generationId, status);
			// Shared with fresh submission (see subscriptions.ts): the FIRST
			// caller for this (subscriptionOwner, generationId) pair -- whether
			// that was this pass or the page's own submit-time subscribe -- is
			// the only one that actually triggers `onSubscribe`.
			if (!signal?.aborted) {
				ensureSubscribed(subscriptionOwner, generationId, () => onSubscribe?.(generationId));
			}
			return;
		}

		const outputs =
			status.status === 'completed' ? await fetchOutputs(api, generationId, retryDelayMs, signal) : null;
		if (signal?.aborted) return;
		applyTerminal(generationId, status.status, outputs, status.message ?? null);
	}

	for (const group of chunk(ids, chunkSize)) {
		await Promise.all(group.map((id) => reconcileOne(id, false)));
		if (signal?.aborted) return;
	}
}
