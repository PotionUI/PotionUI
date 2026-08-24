<!--
	Shared browse/search surface for a model-valued form field's picker: fetch
	orchestration (preset-scoped vs global, tag/favorites filters, debounce),
	the Global/Collections view toggle, result rows, loading/empty states, and
	(when `recommendations` is passed - ModelField only) recommended-download
	offers. Selection semantics stay with the caller: this only ever calls
	`onSelect(model)` and never writes into a field's value - single-select vs.
	ordered multi-select-with-weights (see ModelField.svelte / LoraPickerField.svelte)
	is entirely their concern.

	Meant to be conditionally mounted only while the picker is actually open
	(the caller's `{#if showDropdown}`/`{#if showSearch}`) - it fetches once on
	mount, so there is no separate "is this visible" bookkeeping here.
-->
<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { logger } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import { Badge } from '$lib/components/ui';
	import Icon from '../Icon.svelte';
	import ModelResultRow from './ModelResultRow.svelte';
	import ModelCollectionBrowser from './ModelCollectionBrowser.svelte';
	import { buildModelSearchRequest } from '$lib/utils/modelSearchParams';
	import { toggleModelFavoriteOptimistic } from '$lib/utils/modelFavorite';
	import { buildModelPickerEntries, downloadPayloadForRecommendation } from '$lib/utils/modelRecommendations';
	import {
		initialModelDownloadState,
		reduceModelDownloadState,
		type ModelDownloadState
	} from '$lib/utils/modelDownloadState';
	import type { ModelRecommendation } from '$lib/types/api';

	export let modelType: string;
	export let presetId: string = '';
	export let limit: number = 100;
	export let searchQuery: string = '';
	/** User's own tag-filter chips (names, resolved to ids before the fetch). */
	export let tagFilters: string[] = [];
	/** Admin-set `configuration.filter_tags` (already ids) - preset-scoped fetches only. */
	export let filterTagIds: string[] = [];
	export let favoritesOnly: boolean = false;
	/** Model ids to hide from both the Global list and the Collections view
	 *  (e.g. LoraPickerField's already-added rows). */
	export let excludeIds: Set<string> = new Set();
	/** ModelField-only: pins matched recommendations first and offers unmatched
	 *  ones as a download. Never passed by LoraPickerField. */
	export let recommendations: ModelRecommendation[] | null = null;
	export let rowSize: 'sm' | 'md' = 'md';
	export let onSelect: (model: any) => void;
	/** Bindable so a caller with its own "refresh" affordance (ModelField's
	 *  search-input refresh button) can show/disable it against this panel's
	 *  in-flight fetch. */
	export let loading = false;

	let models: any[] = [];
	let pickerView: 'global' | 'collections' = 'global';

	$: visibleModels = models.filter((m) => !excludeIds.has(m.id));
	$: pickerEntries = recommendations
		? buildModelPickerEntries(visibleModels, recommendations)
		: visibleModels.map((model) => ({ kind: 'model' as const, model }));

	async function resolveTagIds(names: string[]): Promise<string[]> {
		if (names.length === 0) return [];
		try {
			const response = await api.getTags('MODEL');
			if (response.success && response.data?.tags) {
				const allTags = response.data.tags;
				return names.map((n) => allTags.find((t: any) => t.name === n)?.id).filter(Boolean);
			}
		} catch (error) {
			logger.error('[ModelBrowserPanel] Failed to resolve tag filters:', error);
		}
		return [];
	}

	async function fetchModels() {
		loading = true;
		try {
			const tagIds = await resolveTagIds(tagFilters);
			const request = buildModelSearchRequest({
				modelType,
				presetId,
				searchQuery,
				limit,
				tagIds,
				anyTagIds: filterTagIds,
				favoritesOnly
			});
			const response =
				request.kind === 'preset'
					? await api.getPresetModels(request.presetId, request.modelType, request.search, request.opts)
					: await api.getModels(request.params);
			if (response.success && response.data?.models) {
				models = response.data.models;
			}
		} catch (error) {
			logger.error('[ModelBrowserPanel] Failed to fetch models:', error);
		} finally {
			loading = false;
		}
	}

	function toggleFavorite(model: any, event: Event) {
		event.stopPropagation();
		event.preventDefault();
		void toggleModelFavoriteOptimistic(
			model,
			(favorite) => {
				models = models.map((m) => (m.id === model.id ? { ...m, is_favorite: favorite } : m));
			},
			'[ModelBrowserPanel]'
		);
	}

	// Fetch on mount, then re-fetch on any filter change - debounced only for a
	// pure search-text edit, immediate for a tag/favorites toggle (matches the
	// pre-extraction behavior in both ModelField.fetchModels and
	// LoraPickerField.runSearch/scheduleSearch).
	let mounted = false;
	let searchDebounceTimer: ReturnType<typeof setTimeout> | null = null;
	let lastParams = { tagFilters: '', searchQuery: '', favoritesOnly: false };

	// Filters are passed as arguments, never read through a helper inside the
	// statement: Svelte's reactive dependencies are syntactic and it does not look
	// inside a called function, so `currentParams()` left all three untracked and
	// this only ever ran once, when `mounted` flipped.
	$: scheduleFilterFetch(tagFilters.join(','), searchQuery, favoritesOnly, mounted);

	function scheduleFilterFetch(
		tagKey: string,
		query: string,
		favOnly: boolean,
		isMounted: boolean
	) {
		if (!isMounted) return;
		const current = { tagFilters: tagKey, searchQuery: query, favoritesOnly: favOnly };
		const searchQueryChanged = current.searchQuery !== lastParams.searchQuery;
		const othersChanged =
			current.tagFilters !== lastParams.tagFilters ||
			current.favoritesOnly !== lastParams.favoritesOnly;
		if (!searchQueryChanged && !othersChanged) return;

		if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
		if (searchQueryChanged && !othersChanged) {
			searchDebounceTimer = setTimeout(() => {
				fetchModels();
				lastParams = current;
			}, 300);
		} else {
			fetchModels();
			lastParams = current;
		}
	}

	onMount(() => {
		lastParams = { tagFilters: tagFilters.join(','), searchQuery, favoritesOnly };
		fetchModels();
		mounted = true;
	});

	/** Force-refetch with the current filters, bypassing the debounce/no-change
	 *  guard above - used by a caller's own "refresh" button (see ModelField.svelte). */
	export function refresh() {
		fetchModels();
	}

	onDestroy(() => {
		if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
		Object.values(pollTimers).forEach(clearInterval);
	});

	// --- Recommendation download offers (ModelField only - `recommendations` unset otherwise) ---
	let downloadStates: Record<string, ModelDownloadState> = {};
	let pollTimers: Record<string, ReturnType<typeof setInterval>> = {};

	function downloadStateFor(recommendation: ModelRecommendation): ModelDownloadState {
		return downloadStates[recommendation.name] || initialModelDownloadState;
	}

	function setDownloadState(recommendation: ModelRecommendation, next: ModelDownloadState) {
		downloadStates = { ...downloadStates, [recommendation.name]: next };
	}

	async function startRecommendationDownload(recommendation: ModelRecommendation) {
		setDownloadState(recommendation, reduceModelDownloadState(downloadStateFor(recommendation), { type: 'start' }));
		try {
			const payload = downloadPayloadForRecommendation(recommendation, modelType);
			const response = await api.startModelDownload(payload);
			if (!response.success || !response.data?.download_id) {
				throw new Error(response.message || 'Failed to start download');
			}
			setDownloadState(
				recommendation,
				reduceModelDownloadState(downloadStateFor(recommendation), {
					type: 'started',
					downloadId: response.data.download_id
				})
			);
			pollRecommendationDownload(recommendation, response.data.download_id);
		} catch (error: any) {
			if (error?.response?.status === 403) {
				setDownloadState(recommendation, reduceModelDownloadState(downloadStateFor(recommendation), { type: 'forbidden' }));
				return;
			}
			logger.error('[ModelBrowserPanel] Failed to start model download:', error);
			setDownloadState(
				recommendation,
				reduceModelDownloadState(downloadStateFor(recommendation), {
					type: 'error',
					message: error?.response?.data?.message || 'Failed to start download'
				})
			);
		}
	}

	function pollRecommendationDownload(recommendation: ModelRecommendation, downloadId: string) {
		const key = recommendation.name;
		if (pollTimers[key]) clearInterval(pollTimers[key]);
		pollTimers[key] = setInterval(async () => {
			try {
				const response = await api.getModelDownloadStatus(downloadId);
				if (!response.success || !response.data) return;
				const { status, progress, error } = response.data;
				setDownloadState(recommendation, reduceModelDownloadState(downloadStateFor(recommendation), { type: 'poll', status, progress, error }));
				if (status === 'completed' || status === 'failed') {
					clearInterval(pollTimers[key]);
					delete pollTimers[key];
					if (status === 'completed') await fetchModels();
				}
			} catch (error) {
				logger.error('[ModelBrowserPanel] Failed to poll model download status:', error);
			}
		}, 2000);
	}
</script>

<!-- View toggle: Collections vs. the flat Global list -->
<div class="sticky top-0 z-10 flex items-center gap-0.5 p-1 bg-surface-1 border-b border-line">
	<button
		type="button"
		on:click={() => (pickerView = 'collections')}
		class="flex-1 inline-flex items-center justify-center gap-1 px-2 py-1 text-xs rounded transition-colors {pickerView === 'collections' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
		aria-pressed={pickerView === 'collections'}
	>
		<Icon name="folder" className="w-3.5 h-3.5" />
		Collections
	</button>
	<button
		type="button"
		on:click={() => (pickerView = 'global')}
		class="flex-1 inline-flex items-center justify-center gap-1 px-2 py-1 text-xs rounded transition-colors {pickerView === 'global' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
		aria-pressed={pickerView === 'global'}
	>
		<Icon name="model" className="w-3.5 h-3.5" />
		Global
	</button>
</div>

{#if pickerView === 'collections'}
	<ModelCollectionBrowser {modelType} {limit} search={searchQuery} {excludeIds} {onSelect} />
{:else if loading}
	<div class="p-3 text-center text-fg-muted text-sm">Loading models...</div>
{:else if pickerEntries.length > 0}
	{#each pickerEntries as entry}
		{#if entry.kind === 'recommended-download'}
			{@const recommendation = entry.recommendation}
			{@const downloadState = downloadStates[recommendation.name] || initialModelDownloadState}
			<div class="w-full flex gap-3 items-center p-2.5 border-b border-line last:border-b-0 border-l-2 border-l-info text-left">
				<div class="w-12 h-12 shrink-0 bg-surface-3 rounded-md flex items-center justify-center text-fg-subtle">
					<Icon name="download" className="w-5 h-5" />
				</div>
				<div class="flex-1 min-w-0">
					<div class="flex items-center gap-1.5 min-w-0">
						<span class="text-sm font-medium text-fg truncate" title={recommendation.name}>{recommendation.name}</span>
						<Badge variant="info" size="sm">Suggested</Badge>
					</div>
					{#if recommendation.description}
						<div class="truncate text-xs text-fg-subtle mt-0.5">{recommendation.description}</div>
					{/if}
					{#if recommendation.size}
						<div class="font-mono text-2xs tabular-nums text-fg-subtle mt-0.5">{recommendation.size}</div>
					{/if}
					{#if downloadState.phase === 'starting' || downloadState.phase === 'polling'}
						<div class="mt-1.5 flex items-center gap-2">
							<div class="h-1 flex-1 bg-surface-3 rounded-sm overflow-hidden">
								<div
									class="h-full bg-signal-solid transition-all duration-300"
									style="width: {Math.round((downloadState.progress ?? 0) * 100)}%"
								></div>
							</div>
							<span class="font-mono text-2xs tabular-nums text-fg-muted">
								{downloadState.progress != null ? `${Math.round(downloadState.progress * 100)}%` : '…'}
							</span>
						</div>
					{:else if downloadState.phase === 'failed'}
						<div class="mt-1 text-2xs text-danger">{downloadState.error || 'Download failed'}</div>
					{:else if downloadState.phase === 'forbidden'}
						<div class="mt-1 text-2xs text-fg-subtle">Admin permission required to download this model.</div>
					{/if}
				</div>
				{#if downloadState.phase !== 'forbidden'}
					<button
						type="button"
						on:click|stopPropagation={() => startRecommendationDownload(recommendation)}
						disabled={downloadState.phase === 'starting' || downloadState.phase === 'polling'}
						class="shrink-0 p-1.5 hover:bg-surface-3 rounded text-fg-muted disabled:opacity-50"
						title={downloadState.phase === 'completed' ? 'Downloaded' : 'Download this model'}
					>
						<Icon
							name={downloadState.phase === 'completed' ? 'check' : 'download'}
							className="w-4 h-4 {downloadState.phase === 'polling' ? 'animate-spin' : ''}"
						/>
					</button>
				{/if}
			</div>
		{:else}
			<ModelResultRow
				model={entry.model}
				size={rowSize}
				{onSelect}
				onToggleFavorite={toggleFavorite}
				accented={entry.kind === 'recommended-model'}
			>
				<svelte:fragment slot="badge">
					{#if entry.kind === 'recommended-model'}
						<Badge variant="info" size="sm">Suggested</Badge>
					{/if}
				</svelte:fragment>
			</ModelResultRow>
		{/if}
	{/each}
{:else}
	<div class="p-3 text-center text-fg-muted text-sm">No models found</div>
{/if}

<style>
	:global(.animate-spin) {
		animation: spin 1s linear infinite;
	}

	@keyframes spin {
		from {
			transform: rotate(0deg);
		}
		to {
			transform: rotate(360deg);
		}
	}
</style>
