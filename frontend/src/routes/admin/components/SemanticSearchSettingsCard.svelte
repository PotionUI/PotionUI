<script lang="ts">
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import { onMount, onDestroy } from 'svelte';
	import * as adminApi from '$lib/services/admin-api';
	import { downloadStore } from '$lib/stores/downloads';
	import { downloaderWebSocket } from '$lib/services/downloaderWebsocket';
	import { Button, Badge } from '$lib/components/ui';

	export let settings: Record<string, any>;
	export let onSettingChange: (key: string, value: unknown) => void;

	type ModelKind = 'prompt_embedding' | 'media_tagger' | 'media_vision';
	type FetchStatus = 'idle' | 'checking' | 'ready' | 'queued' | 'downloading' | 'failed';

	interface FetchState {
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

	let fetchState: Record<ModelKind, FetchState> = {
		prompt_embedding: initialFetchState(),
		media_tagger: initialFetchState(),
		media_vision: initialFetchState()
	};

	const downloadIdToKind: Record<string, ModelKind> = {};

	function modelNameFor(kind: ModelKind): string {
		switch (kind) {
			case 'prompt_embedding':
				return settings.prompt_embedding_model;
			case 'media_tagger':
				return settings.media_tagger_model;
			case 'media_vision':
				return settings.media_vision_model;
		}
	}

	/** A backend job that isn't downloading yet still reads as an active fetch. */
	function statusFromDownload(downloadStatus: adminApi.ActiveModelDownload['status']): FetchStatus {
		return downloadStatus === 'pending' ? 'queued' : 'downloading';
	}

	// Reconstructs "a fetch for this asset is already running" from the
	// status response alone - the page-local downloadId->kind map used to be
	// the only place this lived, so it started every reload/reconnect back
	// at idle even mid-download. Re-subscribing here (not just recording the
	// id) is what lets the already-open WebSocket start delivering progress
	// for a job this page never queued itself.
	function applyActiveDownload(kind: ModelKind, active: adminApi.ActiveModelDownload | null): void {
		if (!active) return;
		downloadIdToKind[active.id] = kind;
		downloaderWebSocket.subscribeToDownload(active.id);
	}

	async function refreshStatus(kind: ModelKind): Promise<void> {
		fetchState = { ...fetchState, [kind]: { ...fetchState[kind], status: 'checking', error: null } };
		try {
			if (kind === 'prompt_embedding') {
				const res = await adminApi.getPromptEmbeddingStatus(modelNameFor('prompt_embedding'));
				if (res.success && res.data) {
					const active = res.data.active_download;
					applyActiveDownload('prompt_embedding', active);
					fetchState = {
						...fetchState,
						prompt_embedding: {
							...fetchState.prompt_embedding,
							status: active ? statusFromDownload(active.status) : res.data.present ? 'ready' : 'idle',
							path: res.data.path,
							size: res.data.size,
							downloadId: active?.id ?? null,
							progress: active?.progress ?? 0,
							loaded: res.data.loaded
						}
					};
				}
			} else {
				const res = await adminApi.getMediaModelsStatus({
					taggerModel: modelNameFor('media_tagger'),
					visionModel: modelNameFor('media_vision')
				});
				if (res.success && res.data) {
					const taggerActive = res.data.tagger.active_download;
					const visionActive = res.data.vision.active_download;
					applyActiveDownload('media_tagger', taggerActive);
					applyActiveDownload('media_vision', visionActive);
					fetchState = {
						...fetchState,
						media_tagger: {
							...fetchState.media_tagger,
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
						},
						media_vision: {
							...fetchState.media_vision,
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
						}
					};
				}
			}
		} catch (err) {
			fetchState = {
				...fetchState,
				[kind]: { ...fetchState[kind], status: 'failed', error: getErrorMessage(err) }
			};
			logger.error(`Failed to read ${kind} model status:`, err);
		}
	}

	async function fetchModel(kind: ModelKind): Promise<void> {
		await refreshStatus(kind);
		const target = fetchState[kind];
		if (!target.path) return;
		// refreshStatus may have just discovered a job already running for
		// this asset (e.g. queued from another tab, or recovered on this
		// page's own reload) - queueing another would start a duplicate.
		if (target.status === 'queued' || target.status === 'downloading') return;

		fetchState = { ...fetchState, [kind]: { ...fetchState[kind], status: 'queued', error: null } };

		const download = await downloadStore.queueHfRepoDownload(modelNameFor(kind), {
			destination_dir: target.path
		});

		if (!download) {
			fetchState = {
				...fetchState,
				[kind]: { ...fetchState[kind], status: 'failed', error: 'Failed to queue download' }
			};
			return;
		}

		downloadIdToKind[download.id] = kind;
		fetchState = {
			...fetchState,
			[kind]: {
				...fetchState[kind],
				status: download.status === 'completed' ? 'ready' : 'downloading',
				downloadId: download.id,
				progress: download.progress || 0
			}
		};
	}

	function isFetchDisabled(state: FetchState): boolean {
		return state.status === 'checking' || state.status === 'queued' || state.status === 'downloading';
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
			fetchState = {
				...fetchState,
				[kind]: { ...fetchState[kind], status: 'downloading', progress: update.progress }
			};
		});

		statusUnsubscribe = downloaderWebSocket.onDownloadStatus((update) => {
			const kind = downloadIdToKind[update.download_id];
			if (!kind) return;
			if (update.status === 'completed') {
				fetchState = {
					...fetchState,
					[kind]: { ...fetchState[kind], status: 'ready', progress: 1 }
				};
				refreshStatus(kind);
			} else if (update.status === 'failed' || update.status === 'cancelled') {
				fetchState = {
					...fetchState,
					[kind]: { ...fetchState[kind], status: 'failed', error: update.error || null }
				};
			} else {
				fetchState = { ...fetchState, [kind]: { ...fetchState[kind], status: 'downloading' } };
			}
		});

		await Promise.all([refreshStatus('prompt_embedding'), refreshStatus('media_tagger')]);
	});

	onDestroy(() => {
		progressUnsubscribe?.();
		statusUnsubscribe?.();
		downloaderWebSocket.disconnect();
	});

	function onNumberInput(key: string, e: Event) {
		const value = (e.currentTarget as HTMLInputElement).value;
		onSettingChange(key, value === '' ? null : Number(value));
	}
</script>

{#snippet fetchControl(kind: ModelKind)}
	{@const state = fetchState[kind]}
	<div class="flex items-center gap-3 flex-shrink-0">
		{#if state.status === 'ready'}
			<Badge variant="success">
				Ready{#if state.size}&nbsp;&middot; {downloadStore.formatBytes(state.size)}{/if}
			</Badge>
			{#if state.loaded}
				<span title="Currently resident in memory">
					<Badge variant="signal" size="sm" dot>In memory</Badge>
				</span>
			{/if}
		{:else if state.status === 'downloading' || state.status === 'queued'}
			<span class="font-mono text-xs tabular-nums text-fg-muted"
				>{state.status === 'queued' ? 'queued' : `${Math.round(state.progress * 100)}%`}</span
			>
			<Badge variant="info">Downloading</Badge>
		{:else if state.status === 'failed'}
			<span title={state.error ?? undefined}><Badge variant="danger">Failed</Badge></span>
		{:else if state.status === 'checking'}
			<span class="text-xs text-fg-muted">Checking...</span>
		{/if}
		<Button
			size="sm"
			variant="secondary"
			disabled={isFetchDisabled(state)}
			loading={state.status === 'checking' || state.status === 'queued'}
			onclick={() => fetchModel(kind)}
		>
			Fetch
		</Button>
	</div>
{/snippet}

<div class="bg-surface-1 rounded-lg border border-line shadow-raised">
	<div class="px-6 py-3 border-b border-line">
		<h3 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">
			Semantic Search &amp; Media Indexing
		</h3>
	</div>

	<div class="px-6 divide-y divide-line">
		<!-- Prompt search -->
		<div class="py-4 space-y-4">
			<h4 class="text-sm font-medium text-fg">Prompt search</h4>

			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="prompt-embedding-provider" class="block text-sm font-medium text-fg mb-1">
						Embedding provider
					</label>
					<p class="text-sm text-fg-muted">Backend used to embed saved prompts for semantic search</p>
				</div>
				<select
					id="prompt-embedding-provider"
					class="input w-48 flex-shrink-0"
					value={settings.prompt_embedding_provider || 'local'}
					on:change={(e) => onSettingChange('prompt_embedding_provider', e.currentTarget.value)}
				>
					<option value="local">Local</option>
					<option value="ollama">Ollama</option>
				</select>
			</div>

			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="prompt-embedding-model" class="block text-sm font-medium text-fg mb-1">
						Model
					</label>
					<p class="text-sm text-fg-muted">
						{settings.prompt_embedding_provider === 'ollama'
							? 'Ollama model name'
							: 'Hugging Face model id'}
					</p>
				</div>
				<input
					id="prompt-embedding-model"
					type="text"
					class="input w-64 flex-shrink-0 font-mono text-sm"
					value={settings.prompt_embedding_model || ''}
					on:input={(e) => onSettingChange('prompt_embedding_model', e.currentTarget.value)}
				/>
			</div>

			{#if settings.prompt_embedding_provider === 'ollama'}
				<div class="flex items-start justify-between gap-6">
					<div>
						<label for="prompt-embedding-ollama-base-url" class="block text-sm font-medium text-fg mb-1">
							Ollama base URL
						</label>
						<p class="text-sm text-fg-muted">Base URL of the Ollama server</p>
					</div>
					<input
						id="prompt-embedding-ollama-base-url"
						type="text"
						class="input w-64 flex-shrink-0 font-mono text-sm"
						value={settings.prompt_embedding_ollama_base_url || ''}
						on:input={(e) =>
							onSettingChange('prompt_embedding_ollama_base_url', e.currentTarget.value)}
					/>
				</div>

				<div class="flex items-start justify-between gap-6">
					<div>
						<label for="prompt-embedding-ollama-model" class="block text-sm font-medium text-fg mb-1">
							Ollama model
						</label>
						<p class="text-sm text-fg-muted">Model served by the Ollama instance above</p>
					</div>
					<input
						id="prompt-embedding-ollama-model"
						type="text"
						class="input w-64 flex-shrink-0 font-mono text-sm"
						value={settings.prompt_embedding_ollama_model || ''}
						on:input={(e) => onSettingChange('prompt_embedding_ollama_model', e.currentTarget.value)}
					/>
				</div>
			{:else}
				<div class="flex items-start justify-between gap-6">
					<div>
						<label for="prompt-embedding-device" class="block text-sm font-medium text-fg mb-1">
							Device
						</label>
						<p class="text-sm text-fg-muted">Device the local embedder runs on</p>
					</div>
					<select
						id="prompt-embedding-device"
						class="input w-48 flex-shrink-0"
						value={settings.prompt_embedding_device || 'cpu'}
						on:change={(e) => onSettingChange('prompt_embedding_device', e.currentTarget.value)}
					>
						<option value="cpu">CPU</option>
						<option value="cuda">CUDA</option>
					</select>
				</div>

				<div class="flex items-start justify-between gap-6">
					<div>
						<label for="prompt-embedding-auto-download" class="block text-sm font-medium text-fg mb-1">
							Auto-download
						</label>
						<p class="text-sm text-fg-muted">Fetch weights automatically on first use</p>
					</div>
					<input
						type="checkbox"
						id="prompt-embedding-auto-download"
						class="w-4 h-4 mt-1 text-signal border-line-strong rounded focus:ring-signal flex-shrink-0"
						checked={settings.prompt_embedding_auto_download ?? false}
						on:change={(e) =>
							onSettingChange('prompt_embedding_auto_download', e.currentTarget.checked)}
					/>
				</div>

				<div class="flex items-start justify-between gap-6">
					<div>
						<span class="block text-sm font-medium text-fg mb-1">Model weights</span>
						<p class="text-xs font-mono text-fg-muted truncate max-w-md">
							{fetchState.prompt_embedding.path ?? '—'}
						</p>
					</div>
					{@render fetchControl('prompt_embedding')}
				</div>
			{/if}
		</div>

		<!-- Media tagging -->
		<div class="py-4 space-y-4">
			<h4 class="text-sm font-medium text-fg">Media tagging</h4>

			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="media-tagger-model" class="block text-sm font-medium text-fg mb-1">Model</label>
					<p class="text-sm text-fg-muted">Hugging Face id of the local WD tagger checkpoint</p>
				</div>
				<input
					id="media-tagger-model"
					type="text"
					class="input w-64 flex-shrink-0 font-mono text-sm"
					value={settings.media_tagger_model || ''}
					on:input={(e) => onSettingChange('media_tagger_model', e.currentTarget.value)}
				/>
			</div>

			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="media-tagger-device" class="block text-sm font-medium text-fg mb-1">Device</label>
					<p class="text-sm text-fg-muted">Device the tagger model runs on</p>
				</div>
				<select
					id="media-tagger-device"
					class="input w-48 flex-shrink-0"
					value={settings.media_tagger_device || 'cpu'}
					on:change={(e) => onSettingChange('media_tagger_device', e.currentTarget.value)}
				>
					<option value="cpu">CPU</option>
					<option value="cuda">CUDA</option>
				</select>
			</div>

			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="media-tagger-auto-download" class="block text-sm font-medium text-fg mb-1">
						Auto-download
					</label>
					<p class="text-sm text-fg-muted">Fetch weights automatically on first use</p>
				</div>
				<input
					type="checkbox"
					id="media-tagger-auto-download"
					class="w-4 h-4 mt-1 text-signal border-line-strong rounded focus:ring-signal flex-shrink-0"
					checked={settings.media_tagger_auto_download ?? false}
					on:change={(e) => onSettingChange('media_tagger_auto_download', e.currentTarget.checked)}
				/>
			</div>

			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="media-tagger-tag-threshold" class="block text-sm font-medium text-fg mb-1">
						Tag threshold
					</label>
					<p class="text-sm text-fg-muted">Minimum confidence to store a general tag</p>
				</div>
				<input
					id="media-tagger-tag-threshold"
					type="number"
					min="0"
					max="1"
					step="0.05"
					class="input w-24 flex-shrink-0 font-mono tabular-nums text-sm"
					value={settings.media_tagger_tag_threshold ?? 0.35}
					on:input={(e) => onNumberInput('media_tagger_tag_threshold', e)}
				/>
			</div>

			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="media-tagger-character-threshold" class="block text-sm font-medium text-fg mb-1">
						Character threshold
					</label>
					<p class="text-sm text-fg-muted">Minimum confidence to store a character tag</p>
				</div>
				<input
					id="media-tagger-character-threshold"
					type="number"
					min="0"
					max="1"
					step="0.05"
					class="input w-24 flex-shrink-0 font-mono tabular-nums text-sm"
					value={settings.media_tagger_character_threshold ?? 0.75}
					on:input={(e) => onNumberInput('media_tagger_character_threshold', e)}
				/>
			</div>

			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="media-nsfw-blur-threshold" class="block text-sm font-medium text-fg mb-1">
						NSFW blur threshold
					</label>
					<p class="text-sm text-fg-muted">
						Blur gallery media when questionable + explicit ratings reach this value
					</p>
				</div>
				<input
					id="media-nsfw-blur-threshold"
					type="number"
					min="0"
					max="1"
					step="0.05"
					class="input w-24 flex-shrink-0 font-mono tabular-nums text-sm"
					value={settings.media_nsfw_blur_threshold ?? 0.6}
					on:input={(e) => onNumberInput('media_nsfw_blur_threshold', e)}
				/>
			</div>

			<div class="flex items-start justify-between gap-6">
				<div>
					<span class="block text-sm font-medium text-fg mb-1">Model weights</span>
					<p class="text-xs font-mono text-fg-muted truncate max-w-md">
						{fetchState.media_tagger.path ?? '—'}
					</p>
				</div>
				{@render fetchControl('media_tagger')}
			</div>
		</div>

		<!-- Visual search -->
		<div class="py-4 space-y-4">
			<h4 class="text-sm font-medium text-fg">Visual search</h4>

			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="media-vision-model" class="block text-sm font-medium text-fg mb-1">Model</label>
					<p class="text-sm text-fg-muted">Hugging Face id of the SigLIP checkpoint</p>
				</div>
				<input
					id="media-vision-model"
					type="text"
					class="input w-64 flex-shrink-0 font-mono text-sm"
					value={settings.media_vision_model || ''}
					on:input={(e) => onSettingChange('media_vision_model', e.currentTarget.value)}
				/>
			</div>

			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="media-vision-device" class="block text-sm font-medium text-fg mb-1">Device</label>
					<p class="text-sm text-fg-muted">Device the vision embedder runs on</p>
				</div>
				<select
					id="media-vision-device"
					class="input w-48 flex-shrink-0"
					value={settings.media_vision_device || 'cpu'}
					on:change={(e) => onSettingChange('media_vision_device', e.currentTarget.value)}
				>
					<option value="cpu">CPU</option>
					<option value="cuda">CUDA</option>
				</select>
			</div>

			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="media-vision-auto-download" class="block text-sm font-medium text-fg mb-1">
						Auto-download
					</label>
					<p class="text-sm text-fg-muted">Fetch weights automatically on first use</p>
				</div>
				<input
					type="checkbox"
					id="media-vision-auto-download"
					class="w-4 h-4 mt-1 text-signal border-line-strong rounded focus:ring-signal flex-shrink-0"
					checked={settings.media_vision_auto_download ?? false}
					on:change={(e) => onSettingChange('media_vision_auto_download', e.currentTarget.checked)}
				/>
			</div>

			<div class="flex items-start justify-between gap-6">
				<div>
					<span class="block text-sm font-medium text-fg mb-1">Model weights</span>
					<p class="text-xs font-mono text-fg-muted truncate max-w-md">
						{fetchState.media_vision.path ?? '—'}
					</p>
				</div>
				{@render fetchControl('media_vision')}
			</div>
		</div>
	</div>
</div>
