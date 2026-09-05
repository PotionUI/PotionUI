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
// This module never adopts an orphaned tab's queued/running generation as
// its live display itself -- that is `$lib/generation/messages/ownership.ts`
// (`resolveOwnership`/`beginGenerationOwnership`)'s job, triggered by the
// live event that follows once routing/subscription is restored here.
// Duplicating that here would fight it over which one performs the cold
// start.
//
// Every director-run mutation goes through the SAME identity-guarded
// reducers `$lib/generation/messages/directorRuns.ts` uses for live
// WebSocket messages (`withDirectorRunTerminal` requires
// `existing.generationId === generationId`), so a belated restore response
// for a shot that has since been resubmitted under a new generation id is a
// silent no-op here exactly as it is there. The shared-display patch
// (`activeGenerationId`) gets the same treatment by hand: every apply
// re-reads the tab from the store and writes only if it still points at the
// id this lookup was for.
import { get } from 'svelte/store';
import type { Readable } from 'svelte/store';
import type { Tab, GenerationState, QueuedGeneration } from '$lib/types/tabs';
import type { APIResponse, GenerationStatus } from '$lib/types/api';
import { mapGenerationFiles, type RestoredGenerationData } from '$lib/utils/generationOrchestrator';
import { leadIndex } from '$lib/generation/leadFile';
import {
	directorShotIdsFor,
	withDirectorRunTerminal,
	withoutDirectorRunLink
} from '$lib/generation/messages/directorRuns';
import { withoutQueueEntry } from '$lib/generation/messages/ownership';

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
	 *  WebSocket connection. Never called twice for the same id in one
	 *  `reconcileTabGenerations` invocation, and never called for an id this
	 *  tab has since retired (see the file header). */
	onSubscribe?: (generationId: string) => void;
	/** Ids the caller already knows are this tab's from a source OUTSIDE
	 *  persisted tab state (e.g. the live `/api/generations/queue` snapshot,
	 *  scoped to this tab's `tab_id`) -- folded into the same reconciliation
	 *  pass instead of being trusted/merged separately, so a stale or
	 *  delayed snapshot can never re-add an id this pass (or a live event
	 *  racing it) has already resolved as terminal: every id, wherever it
	 *  came from, is re-confirmed against `getGenerationStatus` here. */
	extraCandidateIds?: string[];
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

/** A confirmed 404 (`generation_not_found`, raised by
 *  `GenerationController.get_generation_status`) proves the generation is
 *  gone. Everything else -- no response at all (network down/timeout), a
 *  5xx, or an unexpected response shape -- is a transient failure that must
 *  never be read as "missing": doing so on a disconnect/restart during
 *  reload would silently destroy the only reference back to a generation
 *  that is, in fact, still running. */
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

/** The status lookup already confirmed 'completed' -- a history-fetch
 *  failure here is never read as "maybe not done after all", only as
 *  "no poster yet". One retry (same transient-failure assumption as the
 *  status lookup) before giving up and resolving 'done' with no poster. */
async function fetchOutputs(
	api: ReconcileApi,
	generationId: string,
	retryDelayMs: number,
	isRetry = false
): Promise<RestoredGenerationData | null> {
	try {
		const historyResponse = await api.getGenerationById(generationId, false, true);
		if (historyResponse.success && historyResponse.data) {
			return mapGenerationFiles((historyResponse.data.files as unknown[]) || [], generationId);
		}
	} catch {
		// fall through to retry/give-up below
	}
	if (!isRetry) {
		await delay(retryDelayMs);
		return fetchOutputs(api, generationId, retryDelayMs, true);
	}
	return null;
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

/** True once an id that started this pass as a Director link has, by apply
 *  time, lost that link (its own terminal WebSocket event resolved and
 *  cleared it while this pass was in flight) without becoming this tab's
 *  active generation either -- nothing on the tab claims it any more, so
 *  routing/subscribing it back in would only resurrect a dead run. Never
 *  applies to an id that came from the live queue snapshot or was never
 *  Director-linked in the first place; those have no "link" to retire. */
function isRetiredDirectorLink(tab: Tab, generationId: string, directorLinkIds: ReadonlySet<string>): boolean {
	if (!directorLinkIds.has(generationId)) return false;
	if (tab.activeGenerationId === generationId) return false;
	return directorShotIdsFor(tab, generationId) === null;
}

/**
 * Reconciles every generation id `tabId` has in flight -- persisted
 * (`activeGenerationId`, non-terminal `directorRuns`, `directorRunLinks`
 * keys, already-known `generation.queue` entries) plus any
 * `options.extraCandidateIds` the caller discovered from the live backend
 * queue snapshot -- against the server's authoritative status. Applies
 * terminal results, restores routing + re-subscribes to ones still running,
 * and drops routing for a link this tab has since retired. Safe to call on
 * every reconnect (not just once per mount): a no-op tab (nothing tracked,
 * no candidates) resolves immediately without any network calls.
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

	const readTab = (): Tab | undefined => get(tabsStore).tabs.find((t) => t.id === tabId);

	const seedTab = readTab();
	if (!seedTab) return;

	const directorLinkIds = new Set(Object.keys(seedTab.directorRunLinks || {}));
	const ids = collectInFlightGenerationIds({
		...seedTab,
		queue: seedTab.generation.queue,
		extraCandidateIds: options.extraCandidateIds
	});
	if (ids.length === 0) return;

	const subscribed = new Set<string>();

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
		}
		tabsStore.updateTab(tabId, patch);
	}

	async function reconcileOne(generationId: string, isRetry: boolean): Promise<void> {
		let statusResponse: APIResponse<GenerationStatus> | null = null;
		let thrown: unknown = null;
		try {
			statusResponse = await api.getGenerationStatus(generationId);
		} catch (err) {
			thrown = err;
		}

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
				await reconcileOne(generationId, true);
			}
			return;
		}

		if (status.status === 'pending' || status.status === 'running') {
			const tab = readTab();
			if (!tab || isRetiredDirectorLink(tab, generationId, directorLinkIds)) return;
			applyKeep(generationId, status);
			if (!subscribed.has(generationId)) {
				subscribed.add(generationId);
				onSubscribe?.(generationId);
			}
			return;
		}

		const outputs =
			status.status === 'completed' ? await fetchOutputs(api, generationId, retryDelayMs) : null;
		applyTerminal(generationId, status.status, outputs, status.message ?? null);
	}

	for (const group of chunk(ids, chunkSize)) {
		await Promise.all(group.map((id) => reconcileOne(id, false)));
	}
}
