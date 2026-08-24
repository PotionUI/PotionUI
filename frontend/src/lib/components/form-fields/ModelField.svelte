<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onDestroy } from 'svelte';
	import { browser } from '$app/environment';
	import { api } from '$lib/services/api/index';
	import { modelDisplayName } from '$lib/utils/modelDisplay';
	import { filesWithPreview } from '$lib/utils/modelPreview';
	import {
		modelFilenameStem,
		modelSummaryParts,
		modelTypePresentation
	} from '$lib/utils/modelPresentation';
	import ModelDetailsModal from '$lib/components/modals/ModelDetailsModal.svelte';
	import ModelBrowserPanel from './ModelBrowserPanel.svelte';
	import portal from '$lib/actions/portal';
	import Icon from '$lib/components/Icon.svelte';
	import { refFor, matchesStoredValue, findModelForValue, MODEL_REF_PREFIX } from '$lib/utils/modelRef';
	import { toggleModelFavoriteOptimistic } from '$lib/utils/modelFavorite';
	import { buildModelSearchRequest } from '$lib/utils/modelSearchParams';
	import { placeholderTint } from '$lib/utils/placeholderTint';
	import type { ModelRecommendation } from '$lib/types/api';


	// Props
	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;

	$: label = config.title || name || '';
	$: description = config.description || '';
	// Everything below historically read from the widget's own configuration
	// block (what FormField used to pass in as `config`) - shadow the raw
	// field config with it so the rest of this file is unchanged.
	$: fieldConfig = config.configuration || config;

	// Model types and configuration
	$: modelType = fieldConfig?.model_type || 'checkpoint';
	$: allowTagFilters = fieldConfig?.allow_tag_filters !== false;
	$: limit = fieldConfig?.limit || 100;
	$: recommendations = (fieldConfig?.recommendations || null) as ModelRecommendation[] | null;
	// Admin-set "base model" tag filter, resolved server-side from the preset's
	// configuration (`filter_tags: "@config:<key>"` or a literal list). Empty/null
	// means no filtering. OR semantics via `any_tag_ids`, independent of the
	// user's own AND tag filter above.
	$: filterTagIds = Array.isArray(fieldConfig?.filter_tags) ? fieldConfig.filter_tags : [];
	// `preset_id` lives on the outer field config (see CarouselField for the
	// same convention) - when present, options are sourced from this preset's
	// engine (union of what its enabled backends can load) instead of the
	// global model library.
	$: presetId = config.preset_id || '';

	// State
	let loading = false;
	let searchQuery = '';
	let showDropdown = false;
	let selectedModelData: any = null;
	let tagFilters: string[] = [];
	let showTagInput = false;
	let newTagQuery = '';
	let userIsTyping = false; // Track if user is manually typing
	let inputHasFocus = false; // Track if input is focused
	let inputRef: HTMLDivElement;
	let dropdownRef: HTMLDivElement;
	let favoritesOnly = false;
	// The browse panel that owns the dropdown's search/fetch - see
	// ModelBrowserPanel.svelte. Bound so the refresh button can trigger it.
	let browserPanel: ModelBrowserPanel;

	const DROPDOWN_MAX_HEIGHT = 340; // matches max-h-[340px] below
	let dropdownPosition = { top: 0, bottom: 0, left: 0, width: 0, maxWidth: 0, openUpward: false };

	// The dropdown is rendered into <body> (via the shared `portal` action) so
	// that ancestor `overflow-y-auto` panels can't clip it. That means it is no
	// longer a DOM descendant of the input, so closing is driven by an outside-
	// pointerdown check rather than the input's blur event (blur fires when the
	// user clicks the view toggles or a row's favorite star, which must NOT
	// close the picker).

	function displayName(m: any): string {
		return modelDisplayName(m);
	}

	function summaryParts(m: any): string[] {
		return modelSummaryParts(m);
	}

	function modelPurpose(m: any): string {
		return modelTypePresentation(m?.model_type).purpose;
	}

	function filenameStem(m: any): string {
		return modelFilenameStem(m);
	}

	// Only the compact "selected model" card's own star lives here - the
	// dropdown's rows own their own favorite toggling (ModelBrowserPanel).
	function toggleModelFavorite(m: any, event: Event) {
		event.stopPropagation();
		event.preventDefault();
		void toggleModelFavoriteOptimistic(m, (favorite) => {
			if (selectedModelData?.id === m.id) {
				selectedModelData = { ...selectedModelData, is_favorite: favorite };
			}
		});
	}

	// Modal state
	let isModelDetailsOpen = false;
	let modalModelId: string | null = null;

	// Extract model path from value (can be string or object with modelPath)
	$: modelPath = typeof value === 'string' ? value : value?.modelPath || '';

	// Initialize tag filters from value or config
	$: {
		if (value && typeof value === 'object' && value.tagFilters) {
			tagFilters = value.tagFilters;
		} else if (fieldConfig?.tags && tagFilters.length === 0) {
			tagFilters = fieldConfig.tags;
		}
	}

	// Update search query when modelPath changes (for session loading)
	$: if (modelPath && modelPath.trim() !== '') {
		// Only meaningful for legacy (path/filename) values - a `model:<id>` ref
		// has nothing displayable until the model row is resolved below.
		if (!modelPath.startsWith(MODEL_REF_PREFIX)) {
			const pathParts = modelPath.split('/');
			const filename = pathParts[pathParts.length - 1];
			const filenameWithoutExt = filename.replace(/\.[^/.]+$/, '');

			// Only update search query if user is not actively typing AND input doesn't have focus
			if (!userIsTyping && !inputHasFocus) {
				searchQuery = filenameWithoutExt;
			}
		}

		// Resolve the selected model's own data whenever it isn't already resolved.
		if (!selectedModelData || !matchesStoredValue(selectedModelData, modelPath)) {
			fetchSelectedModel();
		}
	} else if (!modelPath || modelPath.trim() === '') {
		// Clear selected model data when modelPath is empty
		if (selectedModelData) {
			selectedModelData = null;
		}
		if (searchQuery && !userIsTyping && !inputHasFocus) {
			searchQuery = '';
		}
	}

	async function fetchSelectedModel() {
		try {
			if (modelPath.startsWith(MODEL_REF_PREFIX)) {
				const modelId = modelPath.slice(MODEL_REF_PREFIX.length);
				const response = await api.getModelById(modelId, true);
				if (response.success && response.data?.model) {
					selectedModelData = response.data.model;
				}
				return;
			}

			// Legacy value (file_path or bare filename) - resolve against the same
			// source the picker itself uses, so a preset-scoped field doesn't
			// resolve against models unavailable to its engine.
			const pathParts = modelPath.split('/');
			const filename = pathParts[pathParts.length - 1];

			const request = buildModelSearchRequest({ modelType, presetId, searchQuery: filename, limit: 10 });
			const response =
				request.kind === 'preset'
					? await api.getPresetModels(request.presetId, request.modelType, request.search, request.opts)
					: await api.getModels(request.params);
			const list = response.data?.models || [];

			const foundModel = findModelForValue(modelPath, list);
			if (foundModel) {
				selectedModelData = foundModel;
			}
		} catch (error) {
			logger.error('Failed to fetch selected model:', error);
		}
	}

	function handleSelectionChange(model: any) {
		// Preserve tag filters when changing model selection
		const newValue = {
			modelPath: refFor(model),
			tagFilters: tagFilters
		};
		if (name) {
			onChange(name, newValue);
		} else {
			logger.warn('[ModelField] name is null/undefined, onChange not called!');
		}
		selectedModelData = model;
		searchQuery = (displayName(model) || model.filename || '').replace(/\.[^/.]+$/, '');
		showDropdown = false;
		userIsTyping = false; // Reset typing flag when model is selected
		inputHasFocus = false; // Reset focus flag
	}

	function calculateDropdownPosition() {
		if (!inputRef) return;
		const rect = inputRef.getBoundingClientRect();
		const viewportHeight = window.innerHeight;
		const spaceBelow = viewportHeight - rect.bottom;
		const spaceAbove = rect.top;
		const gap = 4;

		dropdownPosition = {
			top: rect.bottom + gap,
			bottom: viewportHeight - rect.top + gap,
			left: rect.left,
			width: rect.width,
			maxWidth: Math.min(rect.width * 2, window.innerWidth - rect.left - 16),
			openUpward: spaceBelow < DROPDOWN_MAX_HEIGHT && spaceAbove > spaceBelow
		};
	}

	function handleInputFocus() {
		inputHasFocus = true;
		userIsTyping = false;
		calculateDropdownPosition();
		showDropdown = true;
	}

	function handleInputBlur() {
		// Focus left the input (possibly for a button inside the dropdown).
		// Never close here - handleWindowPointerDown owns that.
		inputHasFocus = false;
	}

	function handleWindowPointerDown(event: PointerEvent) {
		if (!showDropdown) return;
		const target = event.target as Node;
		if (inputRef?.contains(target) || dropdownRef?.contains(target)) return;
		showDropdown = false;
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		if (showDropdown && event.key === 'Escape') showDropdown = false;
	}

	// The dropdown is anchored to the input in viewport coordinates, so any
	// scroll or resize of an ancestor must re-anchor it.
	function handleReposition() {
		if (showDropdown) calculateDropdownPosition();
	}

	if (browser) {
		window.addEventListener('pointerdown', handleWindowPointerDown, true);
		window.addEventListener('keydown', handleWindowKeydown);
		window.addEventListener('scroll', handleReposition, true);
		window.addEventListener('resize', handleReposition);
	}

	onDestroy(() => {
		if (!browser) return;
		window.removeEventListener('pointerdown', handleWindowPointerDown, true);
		window.removeEventListener('keydown', handleWindowKeydown);
		window.removeEventListener('scroll', handleReposition, true);
		window.removeEventListener('resize', handleReposition);
	});

	function handleClearSearch() {
		searchQuery = '';
		const newValue = {
			modelPath: '',
			tagFilters: tagFilters
		};
		if (name) {
			onChange(name, newValue);
		}
		selectedModelData = null;
		userIsTyping = false; // Reset typing flag when clearing
		// Keep inputHasFocus as-is since user might still be focused on the input
	}

	function handleInput() {
		userIsTyping = true; // User is manually typing
	}

	function addTagFilter() {
		const trimmedTag = newTagQuery.trim();
		if (trimmedTag && !tagFilters.includes(trimmedTag)) {
			tagFilters = [...tagFilters, trimmedTag];
			updateTagFiltersInValue();
		}
		newTagQuery = '';
		showTagInput = false;
	}

	function removeTagFilter(index: number) {
		tagFilters = tagFilters.filter((_, i) => i !== index);
		updateTagFiltersInValue();
	}

	function updateTagFiltersInValue() {
		const currentModelPath = typeof value === 'string' ? value : value?.modelPath || '';
		const newValue = {
			modelPath: currentModelPath,
			tagFilters: tagFilters
		};
		if (name) {
			onChange(name, newValue);
		}
	}

	function handleTagInputKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter') {
			event.preventDefault();
			addTagFilter();
		} else if (event.key === 'Escape') {
			newTagQuery = '';
			showTagInput = false;
		}
	}


	function handleOpenModelDetails() {
		if (selectedModelData?.id && fieldConfig?.allow_info_modal !== false) {
			modalModelId = selectedModelData.id;
			isModelDetailsOpen = true;
		}
	}

	function handleCloseModelDetails() {
		isModelDetailsOpen = false;
		modalModelId = null;
	}
</script>

<div class="field-card">
	<div class="flex items-center justify-between gap-2 flex-wrap mb-2">
		<label class="label !mb-0 text-sm" id={name ? `${name}-label` : undefined}>
			{label}
		</label>

		<!-- Compact tag filters - inline pills, right-aligned on the label row -->
		{#if allowTagFilters}
			{#if showTagInput}
				<!-- Inline tag input -->
				<div class="flex items-center gap-1">
					<input
						type="text"
						autocomplete="off"
						bind:value={newTagQuery}
						on:keydown={handleTagInputKeydown}
						placeholder="Tag name..."
						class="input text-xs py-1 px-2 w-32"
					/>
					<button
						type="button"
						on:click={addTagFilter}
						disabled={!newTagQuery.trim()}
						class="text-xs px-2 py-1 bg-accent text-accent-contrast rounded hover:bg-accent-hover disabled:opacity-50"
					>
						Add
					</button>
					<button
						type="button"
						on:click={() => { newTagQuery = ''; showTagInput = false; }}
						class="text-xs px-2 py-1 text-fg-muted hover:text-fg"
					>
						Cancel
					</button>
				</div>
			{:else}
				<div class="flex flex-wrap items-center justify-end gap-1.5">
					{#each tagFilters as tag, index}
						<span class="inline-flex items-center gap-1 px-1.5 py-0.5 text-2xs rounded border bg-signal/10 text-signal border-signal/25">
							{tag}
							<button
								type="button"
								on:click={() => removeTagFilter(index)}
								class="hover:opacity-70"
								title="Remove filter"
							>
								<svg class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
								</svg>
							</button>
						</span>
					{/each}
					<button
						type="button"
						on:click={() => (showTagInput = true)}
						class="text-2xs text-fg-muted hover:text-fg px-1"
					>
						+ Tag
					</button>
				</div>
			{/if}
		{/if}
	</div>

	{#if description}
		<p id={name ? `${name}-desc` : undefined} class="text-xs text-fg-muted mb-1">{description}</p>
	{/if}

	<!-- Compact selected model card - clickable for details -->
	{#if selectedModelData && modelPath}
		{@const previewFiles = filesWithPreview(selectedModelData)}
		{@const imageFile = previewFiles.find((f: any) => f.file_type === 'image')}
		{@const fallbackFile = previewFiles.find((f: any) => f.thumbnail_small)}
		{@const thumbnailUrl = imageFile?.thumbnail_small || fallbackFile?.thumbnail_small}
		<div
			class="flex items-center gap-2.5 p-2.5 bg-surface-2 border border-line-strong rounded-lg cursor-pointer hover:bg-surface-3 hover:border-line-hover transition-colors"
			on:click={handleOpenModelDetails}
			on:keydown={(e) => e.key === 'Enter' && handleOpenModelDetails()}
			role="button"
			tabindex="0"
		>
			<!-- Small thumbnail -->
			{#if thumbnailUrl}
				<img
					src={thumbnailUrl}
					alt={displayName(selectedModelData)}
					class="shrink-0 w-11 h-11 object-cover rounded"
					loading="lazy"
				/>
			{:else}
				<div
					class="w-11 h-11 shrink-0 bg-surface-3 rounded flex items-center justify-center text-fg-subtle"
					style={placeholderTint(displayName(selectedModelData))}
				>
					<svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="2"
							d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
						/>
					</svg>
				</div>
			{/if}

			<!-- Model info - horizontal -->
			<div class="flex-1 min-w-0">
				<div class="text-sm font-semibold text-fg truncate" title={displayName(selectedModelData)}>
					{displayName(selectedModelData)}
				</div>
				{#if filenameStem(selectedModelData)}
					<div class="truncate font-mono text-2xs text-fg-subtle" title={selectedModelData.filename}>
						File · {filenameStem(selectedModelData)}
					</div>
				{/if}
				<div class="mt-1 flex min-w-0 items-center gap-1 overflow-hidden">
					{#each summaryParts(selectedModelData) as part, index}
						<span class="max-w-[9rem] truncate rounded bg-surface-3 px-1.5 py-0.5 text-2xs text-fg-muted" title={index === 0 ? modelPurpose(selectedModelData) : part}>
							{part}
						</span>
					{/each}
				</div>
			</div>

			<!-- Favorite star -->
			<button
				type="button"
				on:click={(e) => toggleModelFavorite(selectedModelData, e)}
				class="shrink-0 p-1.5 hover:bg-surface-3 rounded {selectedModelData.is_favorite ? 'text-warning' : 'text-fg-muted'}"
				title={selectedModelData.is_favorite ? 'Remove from favorites' : 'Add to favorites'}
			>
				<Icon name="star" className="w-4 h-4" />
			</button>

			<!-- Swap (clear selection to search again) -->
			<button
				type="button"
				on:click|stopPropagation={handleClearSearch}
				class="shrink-0 inline-flex items-center gap-1.5 px-2.5 py-1.5 border border-line-strong rounded text-xs font-medium text-fg-muted hover:bg-surface-3 hover:text-fg hover:border-line-hover transition-colors"
				title="Swap for a different model"
			>
				Swap
			</button>
		</div>
	{/if}

	<!-- Search input with dropdown - only when no model selected -->
	{#if !selectedModelData || !modelPath}
		<div class="relative">
			<div class="relative" bind:this={inputRef}>
				<svg class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-fg-subtle" fill="none" viewBox="0 0 24 24" stroke="currentColor">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
				</svg>
				<input
					type="text"
					autocomplete="off"
					id={name || undefined}
					bind:value={searchQuery}
					on:input={handleInput}
					on:focus={handleInputFocus}
					on:blur={handleInputBlur}
					placeholder={fieldConfig?.placeholder || 'Search models...'}
					class="input pl-8 pr-16 text-sm"
					aria-labelledby={name ? `${name}-label` : undefined}
					aria-describedby={description && name ? `${name}-desc` : undefined}
				/>
				<div class="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1">
					<button
						type="button"
						on:click={() => (favoritesOnly = !favoritesOnly)}
						class="p-1 hover:bg-surface-3 rounded {favoritesOnly ? 'text-warning' : 'text-fg-muted'}"
						title={favoritesOnly ? 'Showing favorites only' : 'Show favorites only'}
						aria-pressed={favoritesOnly}
					>
						<Icon name="star" className="w-4 h-4" />
					</button>
					{#if searchQuery}
						<button
							type="button"
							on:click={handleClearSearch}
							class="p-1 hover:bg-surface-3 rounded text-fg-muted"
							title="Clear"
						>
							<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d="M6 18L18 6M6 6l12 12"
								/>
							</svg>
						</button>
					{/if}
					<button
						type="button"
						on:click={() => browserPanel?.refresh()}
						class="p-1 hover:bg-surface-3 rounded text-fg-muted"
						title="Refresh models"
						disabled={loading}
					>
						<svg
							class="w-4 h-4"
							class:animate-spin={loading}
							fill="none"
							viewBox="0 0 24 24"
							stroke="currentColor"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
							/>
						</svg>
					</button>
				</div>
			</div>

		<!-- Compact dropdown (portalled to <body> so ancestor scroll panes can't clip it) -->
		{#if showDropdown}
			<div
				bind:this={dropdownRef}
				use:portal
				class="fixed z-50 bg-surface-1 border border-line-strong rounded-lg shadow-floating max-h-[340px] overflow-y-auto"
				style="left: {dropdownPosition.left}px; min-width: {dropdownPosition.width}px; width: max-content; max-width: {dropdownPosition.maxWidth}px; {dropdownPosition.openUpward
					? `bottom: ${dropdownPosition.bottom}px`
					: `top: ${dropdownPosition.top}px`}"
			>
				<ModelBrowserPanel
					bind:this={browserPanel}
					bind:loading
					{modelType}
					{presetId}
					{limit}
					{searchQuery}
					{tagFilters}
					{filterTagIds}
					{favoritesOnly}
					{recommendations}
					rowSize="md"
					onSelect={handleSelectionChange}
				/>
			</div>
		{/if}
		</div>
	{/if}
</div>

<!-- Model Details Modal -->
<ModelDetailsModal isOpen={isModelDetailsOpen} modelId={modalModelId} onClose={handleCloseModelDetails} />

<style>
	.animate-spin {
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
