/**
 * Shared "fetch model weights" state machine for the AI settings panels
 * (Prompt Search / Media Tagging / Visual Search). Each panel owns one
 * controller for the lifetime of its own mount — status is checked and any
 * in-flight download's WebSocket re-subscribed to on init, torn down on
 * unmount, so switching between panels doesn't leak connections.
 */
import { onMount, onDestroy } from 'svelte';
import { logger, getErrorMessage } from '$lib/utils/logger';
import * as adminApi from '$lib/services/admin-api';
import { downloadStore } from '$lib/stores/downloads';
import { downloaderWebSocket } from '$lib/services/downloaderWebsocket';

export type ModelKind = 'prompt_embedding' | 'media_tagger' | 'media_vision';
export type FetchStatus = 'idle' | 'checking' | 'ready' | 'queued' | 'downloading' | 'failed';

export interface FetchState {
	status: FetchStatus;
	progress: number;
	error: string | null;
	path: string | null;
	size: number | null;
	downloadId: string | null;
	/** Resident in memory right now - distinct from `status === 'ready'`
	 * (on-disk). A lifecycle-managed model can be present and evicted. */
	loaded: boolean;
}

function initialFetchState(): FetchState {
	return {
		status: 'idle',
		progress: 0,
		error: null,
		path: null,
		size: null,
		downloadId: null,
		loaded: false
	};
}

/** A backend job that isn't downloading yet still reads as an active fetch. */
function statusFromDownload(downloadStatus: adminApi.ActiveModelDownload['status']): FetchStatus {
	return downloadStatus === 'pending' ? 'queued' : 'downloading';
}

export function isFetchDisabled(state: FetchState): boolean {
	return state.status === 'checking' || state.status === 'queued' || state.status === 'downloading';
}

/** Reads each model kind's current id straight from the panel's `settings`
 * prop, so the controller always sees the latest edited value. */
export function modelNameLookup(settings: Record<string, any>): (kind: ModelKind) => string {
	return (kind) => {
		switch (kind) {
			case 'prompt_embedding':
				return settings.prompt_embedding_model;
			case 'media_tagger':
				return settings.media_tagger_model;
			case 'media_vision':
				return settings.media_vision_model;
		}
	};
}

export interface ModelFetchController {
	state: Record<ModelKind, FetchState>;
	fetchModel: (kind: ModelKind) => Promise<void>;
}

/**
 * `kinds` are the model kinds this controller checks/refetches on mount.
 * Prompt embedding hits its own status endpoint; media_tagger/media_vision
 * share one combined endpoint exactly as the pre-split card did, so a panel
 * that renders only one of them still needs both models' names available via
 * `modelNameFor` and will still update the other kind's state as a side
 * effect of that shared call — harmless since the panel never renders it.
 */
export function createModelFetchController(
	kinds: ModelKind[],
	modelNameFor: (kind: ModelKind) => string
): ModelFetchController {
	const state = $state<Record<ModelKind, FetchState>>({
		prompt_embedding: initialFetchState(),
		media_tagger: initialFetchState(),
		media_vision: initialFetchState()
	});

	const downloadIdToKind: Record<string, ModelKind> = {};

	// Reconstructs "a fetch for this asset is already running" from the
	// status response alone - re-subscribing here (not just recording the id)
	// is what lets the already-open WebSocket start delivering progress for a
	// job this panel never queued itself.
	function applyActiveDownload(kind: ModelKind, active: adminApi.ActiveModelDownload | null): void {
		if (!active) return;
		downloadIdToKind[active.id] = kind;
		downloaderWebSocket.subscribeToDownload(active.id);
	}

	async function refreshStatus(): Promise<void> {
		for (const kind of kinds) {
			state[kind] = { ...state[kind], status: 'checking', error: null };
		}
		try {
			if (kinds.includes('prompt_embedding')) {
				const res = await adminApi.getPromptEmbeddingStatus(modelNameFor('prompt_embedding'));
				if (res.success && res.data) {
					const active = res.data.active_download;
					applyActiveDownload('prompt_embedding', active);
					state.prompt_embedding = {
						...state.prompt_embedding,
						status: active ? statusFromDownload(active.status) : res.data.present ? 'ready' : 'idle',
						path: res.data.path,
						size: res.data.size,
						downloadId: active?.id ?? null,
						progress: active?.progress ?? 0,
						loaded: res.data.loaded
					};
				}
			}

			if (kinds.includes('media_tagger') || kinds.includes('media_vision')) {
				const res = await adminApi.getMediaModelsStatus({
					taggerModel: modelNameFor('media_tagger'),
					visionModel: modelNameFor('media_vision')
				});
				if (res.success && res.data) {
					const taggerActive = res.data.tagger.active_download;
					const visionActive = res.data.vision.active_download;
					applyActiveDownload('media_tagger', taggerActive);
					applyActiveDownload('media_vision', visionActive);
					state.media_tagger = {
						...state.media_tagger,
						status: taggerActive
							? statusFromDownload(taggerActive.status)
							: res.data.tagger.present
								? 'ready'
								: 'idle',
						path: res.data.tagger.path,
						size: res.data.tagger.size,
						downloadId: taggerActive?.id ?? null,
						progress: taggerActive?.progress ?? 0,
						loaded: res.data.tagger.loaded
					};
					state.media_vision = {
						...state.media_vision,
						status: visionActive
							? statusFromDownload(visionActive.status)
							: res.data.vision.present
								? 'ready'
								: 'idle',
						path: res.data.vision.path,
						size: res.data.vision.size,
						downloadId: visionActive?.id ?? null,
						progress: visionActive?.progress ?? 0,
						loaded: res.data.vision.loaded
					};
				}
			}
		} catch (err) {
			for (const kind of kinds) {
				state[kind] = { ...state[kind], status: 'failed', error: getErrorMessage(err) };
			}
			logger.error('Failed to read model status:', err);
		}
	}

	async function fetchModel(kind: ModelKind): Promise<void> {
		await refreshStatus();
		const target = state[kind];
		if (!target.path) return;
		// refreshStatus may have just discovered a job already running for
		// this asset (e.g. queued from another tab, or recovered on this
		// panel's own mount) - queueing another would start a duplicate.
		if (target.status === 'queued' || target.status === 'downloading') return;

		state[kind] = { ...state[kind], status: 'queued', error: null };

		const download = await downloadStore.queueHfRepoDownload(modelNameFor(kind), {
			destination_dir: target.path
		});

		if (!download) {
			state[kind] = { ...state[kind], status: 'failed', error: 'Failed to queue download' };
			return;
		}

		downloadIdToKind[download.id] = kind;
		state[kind] = {
			...state[kind],
			status: download.status === 'completed' ? 'ready' : 'downloading',
			downloadId: download.id,
			progress: download.progress || 0
		};
	}

	let progressUnsubscribe: (() => void) | null = null;
	let statusUnsubscribe: (() => void) | null = null;

	onMount(async () => {
		// Connects (and wires the listeners below) before the status calls
		// further down so a discovered in-flight download can be
		// re-subscribed to and its progress caught immediately - subscribing
		// while disconnected is a silent no-op (see BaseWebSocket.send).
		try {
			await downloaderWebSocket.connectAsync();
		} catch (err) {
			logger.error('Failed to connect downloader WebSocket:', err);
		}

		progressUnsubscribe = downloaderWebSocket.onDownloadProgress((update) => {
			const kind = downloadIdToKind[update.download_id];
			if (!kind) return;
			state[kind] = { ...state[kind], status: 'downloading', progress: update.progress };
		});

		statusUnsubscribe = downloaderWebSocket.onDownloadStatus((update) => {
			const kind = downloadIdToKind[update.download_id];
			if (!kind) return;
			if (update.status === 'completed') {
				state[kind] = { ...state[kind], status: 'ready', progress: 1 };
				refreshStatus();
			} else if (update.status === 'failed' || update.status === 'cancelled') {
				state[kind] = { ...state[kind], status: 'failed', error: update.error || null };
			} else {
				state[kind] = { ...state[kind], status: 'downloading' };
			}
		});

		await refreshStatus();
	});

	onDestroy(() => {
		progressUnsubscribe?.();
		statusUnsubscribe?.();
		downloaderWebSocket.disconnect();
	});

	return { state, fetchModel };
}
