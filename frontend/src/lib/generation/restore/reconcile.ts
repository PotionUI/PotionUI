// Reload/reconnect reconciliation for a tab's in-flight generations
// (routes/generate/+page.svelte's `restoreActiveGenerations`/
// `restoreQueuedGenerations`). Three independent, persisted signals can each
// name a generation the frontend must re-confirm against the server rather
// than trust blindly: `activeGenerationId` (the tab's shared display),
// `directorRuns` (a Video Director shot, keyed by shot id, `queued`/
// `generating`), and `directorRunLinks` (generationId -> shot id(s) -- a
// shot can be tracked ONLY here once its `directorRuns` entry has been
// overwritten by a resubmission). `generation.queue` (unpersisted, but
// possibly already populated this session before a reconnect) is a fourth.
// None subsumes another, so every id from all four is looked up once.
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
	 *  `reconcileTabGenerations` invocation. */
	onSubscribe?: (generationId: string) => void;
	now?: () => number;
}

const DEFAULT_CHUNK_SIZE = 4;
const DEFAULT_RETRY_DELAY_MS = 1500;

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
 *  blindly. See the file header for why none of the four sources subsumes
 *  another. */
export function collectInFlightGenerationIds(
	tab: Pick<Tab, 'activeGenerationId' | 'directorRuns' | 'directorRunLinks'> & {
		queue?: QueuedGeneration[];
	}
): string[] {
	const ids = new Set<string>();
	if (tab.activeGenerationId) ids.add(tab.activeGenerationId);
	for (const run of Object.values(tab.directorRuns || {})) {
		if (run.status === 'queued' || run.status === 'generating') ids.add(run.generationId);
	}
	for (const linkedId of Object.keys(tab.directorRunLinks || {})) ids.add(linkedId);
	for (const queued of tab.queue || []) ids.add(queued.generation_id);
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

/** Mirrors the shared-display patch `restoreActiveGenerations` built inline
 *  for a generation that finished while this tab was disconnected. */
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

/**
 * Reconciles every generation id `tabId` has persisted as in flight
 * (`activeGenerationId`, non-terminal `directorRuns`, `directorRunLinks`
 * keys, and any already-known `generation.queue` entries) against the
 * server's authoritative status, applying terminal results and
 * re-subscribing to ones still running. Safe to call once per tab on
 * reconnect; a no-op tab (nothing tracked as in flight) resolves
 * immediately without any network calls.
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

	const ids = collectInFlightGenerationIds({ ...seedTab, queue: seedTab.generation.queue });
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
		if (!tab || tab.activeGenerationId !== generationId) return;
		tabsStore.updateTab(tabId, {
			generation: {
				...tab.generation,
				isGenerating: true,
				startedAt:
					generationTimestampMs(status.started_at ?? status.created_at) ??
					tab.generation.startedAt ??
					now(),
				currentGeneration: { ...status, id: generationId, generation_id: generationId }
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

		if (thrown !== null || !statusResponse || !statusResponse.success || !statusResponse.data) {
			if (isConfirmedMissing(thrown, statusResponse)) {
				applyMissing(generationId);
				return;
			}
			// Transient: leave `activeGenerationId`/the run untouched and try
			// once more after a delay rather than reading a blip as "gone".
			if (!isRetry) {
				await delay(retryDelayMs);
				await reconcileOne(generationId, true);
			}
			return;
		}

		const status = statusResponse.data;
		if (status.status === 'pending' || status.status === 'running') {
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
