<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { getContext, onDestroy, onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import { toasts } from '$lib/stores/toast';
	import { tabsStore } from '$lib/stores/tabs';
	import { ACTIVE_TAB_ID_CONTEXT_KEY } from '$lib/form/activeTabContext';
	import { registerLoraTriggerSource } from '$lib/stores/activeLoraTriggers';
	import { registerLoraSelectionSource } from '$lib/stores/loraPickerSelections';
	import { combineSegmentsToString } from '$lib/utils/generationOrchestrator';
	import { hasTriggerWordMatch } from '$lib/utils/triggerWords';
	import { copyText } from '$lib/utils/clipboard';
	import { modelDisplayName } from '$lib/utils/modelDisplay';
	import { filesWithPreview } from '$lib/utils/modelPreview';
	import {
		modelFilenameStem,
		modelSummaryParts,
		modelTypePresentation
	} from '$lib/utils/modelPresentation';
	import ModelDetailsModal from '$lib/components/modals/ModelDetailsModal.svelte';
	import ModelBrowserPanel from './ModelBrowserPanel.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { placeholderTint } from '$lib/utils/placeholderTint';
	import { Button, IconButton, Switch } from '$lib/components/ui';
	import type { Model as ModelBase, LoraPickerItem } from '$lib/types/models';
	import { refFor, matchesStoredValue, MODEL_REF_PREFIX } from '$lib/utils/modelRef';
	import { toggleModelFavoriteOptimistic } from '$lib/utils/modelFavorite';
	import {
		parseStrengthInput,
		nudgeStrength,
		toggleLoraStrength,
		isLoraRowDisabled,
		formatStrength
	} from '$lib/utils/loraStrength';
	import { moveItem, dropIndexFor } from '$lib/utils/reorder';
	import { LORA_STRENGTH_KEY, TRIGGERS_KEY } from '$lib/constants/modelMetadata';

	// `file_path` can now be null (a model that exists only on a remote engine
	// backend and was never downloaded here - see docs/models.md); `backend_ids`
	// is the badge data returned by the preset-scoped models endpoint.
	type Model = Omit<ModelBase, 'file_path'> & { file_path: string | null; backend_ids?: string[] };

	// Props
	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;

	$: label = config.title || name || '';
	$: description = config.description || '';
	$: fieldConfig = config.configuration || config;

	// Admin-set "base model" tag filter, resolved server-side from the preset's
	// configuration (`filter_tags: "@config:<key>"`). Empty/null = no filtering.
	$: filterTagIds = Array.isArray(fieldConfig.filter_tags) ? fieldConfig.filter_tags : [];
	$: strengthMin = fieldConfig.strength_min ?? -2;
	$: strengthMax = fieldConfig.strength_max ?? 2;
	$: strengthStep = fieldConfig.strength_step ?? 0.1;
	$: strengthDefault = fieldConfig.strength_default ?? 1;
	$: maxItems = fieldConfig.max_items ?? null;
	$: allowInfoModal = fieldConfig.allow_info_modal !== false;
	$: showTriggers = fieldConfig.show_triggers !== false;
	$: allowTagFilters = fieldConfig.allow_tag_filters !== false;
	$: modelType = fieldConfig.model_type || 'lora';
	// `preset_id` lives on the outer field config (see CarouselField for the
	// same convention) - when present, options are sourced from this preset's
	// engine instead of the global model library.
	$: presetId = config.preset_id || '';

	// Feeds this field's resolved trigger words to the segment editor(s) of the
	// same tab, so they can be highlighted there too (see activeLoraTriggers.ts).
	const activeTabId = getContext<string | undefined>(ACTIVE_TAB_ID_CONTEXT_KEY);
	const triggerSource = registerLoraTriggerSource(activeTabId, name || 'lora_picker');
	const selectionSource = registerLoraSelectionSource(activeTabId, name || 'lora_picker');
	onDestroy(() => {
		triggerSource.unregister();
		selectionSource.unregister();
	});

	// Full library/cache used to resolve thumbnails, triggers and display names
	// for already-selected rows, independent of the search tag filters.
	let models: Model[] = [];
	let libraryLoading = false;

	function resolveModel(storedValue: string, candidates: Model[] = models): Model | undefined {
		if (!storedValue) return undefined;
		return candidates.find((m) => matchesStoredValue(m, storedValue));
	}

	function mergeModels(incoming: Model[]) {
		if (incoming.length === 0) return;
		const byId = new Map(models.map((model) => [model.id, model]));
		for (const model of incoming) byId.set(model.id, model);
		models = Array.from(byId.values());
	}

	$: rows = (Array.isArray(value) ? value : []) as LoraPickerItem[];
	$: atMaxItems = maxItems != null && rows.length >= maxItems;
	// Stable each-keys: the model ref, uniqued with an index suffix on the
	// (add-flow-impossible, but session-blob-possible) duplicate — a keyed each
	// CRASHES on duplicate keys, and a malformed value must degrade, not brick
	// the field. Duplicates fall back to index-keyed behavior only for themselves.
	$: rowKeys = rows.map((r, i) =>
		rows.slice(0, i).some((p) => p.model === r.model) ? `${r.model}#${i}` : r.model
	);
	// Resolved model per row, index-aligned with `rows` (the `{#each rows as row, index
	// (index)}` below is keyed by index). `models` loads/patches in asynchronously
	// (mergeModels); the resolution is inlined here rather than delegated to
	// resolveModel() so this `$:` statement's own dependency scan can see `rows` and
	// `models` directly. Svelte's `$:`/`{@const}` dependency tracking only picks up
	// identifiers referenced in the statement's own expression tree — calling a
	// separately-declared function (even one that reads free variables, or is passed
	// them as an argument) hides those reads and the computed value freezes at
	// whatever it was on first render.
	$: rowModels = rows.map((r) => (r.model ? models.find((m) => matchesStoredValue(m, r.model)) : undefined));
	$: triggerSource.set(rowModels.flatMap((m) => (m ? effectiveTriggerWords(m) : [])));
	$: selectionSource.set(
		rows.map((r, i) => ({
			id:
				rowModels[i]?.id ??
				(r.model?.startsWith(MODEL_REF_PREFIX) ? r.model.slice(MODEL_REF_PREFIX.length) : null),
			name: rowModels[i]
				? modelDisplayName(rowModels[i])
				: (r.model || '').split('/').pop() || '',
			strength: r.strength
		}))
	);
	// file_path is what ModelCollectionBrowser's exclude filter matches on -
	// models with no local file_path (remote-only) can't be excluded there.
	// Keyed on model id: file_path is absent for non-admins and null for remote-only
	// models, so a path-keyed set matched nothing and already-added LoRAs reappeared.
	$: selectedModelIds = new Set(
		rows.map((r, i) => rowModels[i]?.id).filter((id): id is string => !!id)
	);
	// Distinct backends seen across the last preset-scoped fetch - the badge
	// only renders once there is more than one to disambiguate between.

	// Sessions store stable `model:<id>` references, not display metadata. Resolve
	// those ids directly because the selected LoRA may not be on the first page.
	let mounted = false;
	const requestedModelIds = new Set<string>();

	async function hydrateStoredModel(id: string) {
		try {
			const response = await api.getModelById(id, true);
			const model = response.success ? (response.data?.model as Model | undefined) : undefined;
			if (mounted && model?.id === id) {
				mergeModels([model]);
			} else if (mounted) {
				logger.warn(`[LoraPickerField] Could not resolve stored model reference ${id}`);
			}
		} catch (error) {
			logger.error(`[LoraPickerField] Failed to resolve stored model reference ${id}:`, error);
		}
	}

	function hydrateStoredModels(storedValues: string[]) {
		for (const storedValue of new Set(storedValues)) {
			if (!storedValue?.startsWith(MODEL_REF_PREFIX)) continue;
			const id = storedValue.slice(MODEL_REF_PREFIX.length);
			if (!id || requestedModelIds.has(id) || models.some((model) => model.id === id)) continue;
			requestedModelIds.add(id);
			void hydrateStoredModel(id);
		}
	}

	onMount(() => {
		mounted = true;
		return () => {
			mounted = false;
		};
	});

	$: if (mounted && rows.length > 0) {
		hydrateStoredModels(rows.map((row) => row.model));
	}

	// Active prompt text (for trigger chip "already used" state)
	$: activePromptText = (() => {
		const state = $tabsStore;
		const tab = state.tabs.find((t) => t.id === state.activeTabId);
		if (!tab) return '';
		const segments = tab.promptSegments || [];
		return segments.length > 0 ? combineSegmentsToString(segments) : tab.prompt || '';
	})();

	// --- Add / search panel state ---
	let showSearch = false;
	let searchInputRef: HTMLInputElement;
	let searchQuery = '';
	let favoritesOnly = false;

	// Only patches `models` (the already-added rows' library cache) - the add
	// panel's own search results own their favorite toggling (ModelBrowserPanel).
	function toggleModelFavorite(model: Model, event: Event) {
		event.stopPropagation();
		void toggleModelFavoriteOptimistic(
			model,
			(favorite) => {
				models = models.map((m) => (m.id === model.id ? { ...m, is_favorite: favorite } : m));
			},
			'[LoraPickerField]'
		);
	}

	// --- Tag filters (sticky for the lifetime of the mounted field) ---
	let tagFilters: string[] = [];
	let showTagInput = false;
	let newTagQuery = '';
	let tagFiltersSeeded = false;
	let tagInputRef: HTMLInputElement;

	$: if (!tagFiltersSeeded && Array.isArray(fieldConfig.tags) && fieldConfig.tags.length > 0) {
		tagFilters = [...fieldConfig.tags];
		tagFiltersSeeded = true;
	}

	// Modal state
	let isModelDetailsOpen = false;
	let modalModelId: string | null = null;

	async function fetchLibrary() {
		libraryLoading = true;
		try {
			if (presetId) {
				// Preset-scoped. Filters go server-side alongside availability so a
				// LIMITed page stays a full page.
				const response = await api.getPresetModels(presetId, modelType, undefined, {
					limit: 200,
					anyTagIds: filterTagIds.length > 0 ? filterTagIds.join(',') : undefined,
					favoritesOnly: favoritesOnly || undefined
				});
				if (response.success && response.data?.models) {
					const list = response.data.models as Model[];
					mergeModels(list);
				}
				return;
			}

			const response = await api.getModels({
				model_type: modelType,
				include_tags: true,
				limit: 200,
				favorites_only: favoritesOnly || undefined
			});
			if (response.success && response.data?.models) {
				mergeModels(response.data.models as Model[]);
			}
		} catch (error) {
			logger.error('[LoraPickerField] Failed to fetch models:', error);
		} finally {
			libraryLoading = false;
		}
	}

	function openSearch() {
		if (atMaxItems) return;
		closeSwitch();
		showSearch = true;
		searchQuery = '';
		requestAnimationFrame(() => searchInputRef?.focus());
	}

	function closeSearch() {
		showSearch = false;
		searchQuery = '';
	}

	function handleSearchKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') closeSearch();
	}

	// --- Switch: replace a row's model in place, keeping its strength and
	// position (the add-flow above only ever appends a new row). Reuses the
	// same ModelBrowserPanel surface as the add flow and as ModelField's own
	// picker - only one of add-search/switch-search is ever open at a time.
	let switchingIndex: number | null = null;
	let switchSearchQuery = '';

	function openSwitch(index: number) {
		closeSearch();
		switchingIndex = switchingIndex === index ? null : index;
		switchSearchQuery = '';
	}

	function closeSwitch() {
		switchingIndex = null;
		switchSearchQuery = '';
	}

	function handleSwitchSearchKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') closeSwitch();
	}

	function switchRowModel(index: number, model: Model) {
		emit(rows.map((r, i) => (i === index ? { model: refFor(model), strength: r.strength } : r)));
		if (!models.some((m) => m.id === model.id)) {
			models = [...models, model];
		}
		closeSwitch();
	}

	function emit(newRows: LoraPickerItem[]) {
		if (name) {
			onChange(name, newRows);
		}
	}

	// A model's own declared default (if any) wins over the field's configured
	// strength_default - the user's own override (if set) wins over that. See
	// ModelAttributesCard for where these values are edited.
	function suggestedStrength(model: Model): number | undefined {
		const raw = model.user_model_metadata?.[LORA_STRENGTH_KEY] ?? model.model_metadata?.[LORA_STRENGTH_KEY];
		return typeof raw === 'number' ? raw : undefined;
	}

	// Trigger-word highlighting/copying shows the model's own declared
	// triggers UNIONED with the user's personal additions - unlike a scalar
	// attribute like strength, a personal trigger override is additive
	// convenience, not a replacement for the model's official words.
	function effectiveTriggerWords(model: Model): string[] {
		const shared = model.model_metadata?.[TRIGGERS_KEY];
		const own = model.user_model_metadata?.[TRIGGERS_KEY];
		const merged = [...(Array.isArray(shared) ? shared : []), ...(Array.isArray(own) ? own : [])];
		return [...new Set(merged)];
	}

	function selectModel(model: Model) {
		if (atMaxItems) return;
		emit([...rows, { model: refFor(model), strength: suggestedStrength(model) ?? strengthDefault }]);
		// Keep the resolved model info available immediately for the new row.
		if (!models.some((m) => m.id === model.id)) {
			models = [...models, model];
		}
		closeSearch();
	}

	function removeRow(index: number) {
		emit(rows.filter((_, i) => i !== index));
	}

	// --- Drag-to-reorder (grip handle, matches PromptSegment.svelte's pattern) ---
	// `draggable` lives on the row wrapper (so the whole card drags, not just
	// the small handle icon) but is only true while `armedIndex` matches -
	// armed by a pointerdown on the grip, disarmed globally on pointerup so a
	// stray drag never starts from clicking elsewhere on the card.
	let armedIndex: number | null = null;
	let draggingIndex: number | null = null;
	let dragOverIndex: number | null = null;
	let dragOverPosition: 'before' | 'after' | null = null;

	function armDrag(index: number) {
		armedIndex = index;
	}

	function disarmDrag() {
		armedIndex = null;
	}

	function clearDragState() {
		draggingIndex = null;
		dragOverIndex = null;
		dragOverPosition = null;
		armedIndex = null;
	}

	function handleRowDragStart(event: DragEvent, index: number) {
		draggingIndex = index;
		event.dataTransfer?.setData('text/plain', String(index));
		if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
	}

	function handleRowDragOver(event: DragEvent, index: number) {
		if (draggingIndex === null) return;
		event.preventDefault();
		if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
		const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
		dragOverIndex = index;
		dragOverPosition = event.clientY < rect.top + rect.height / 2 ? 'before' : 'after';
	}

	function handleRowDragLeave(index: number) {
		if (dragOverIndex === index) {
			dragOverIndex = null;
			dragOverPosition = null;
		}
	}

	function handleRowDrop(event: DragEvent, index: number) {
		event.preventDefault();
		const from = draggingIndex;
		const position = dragOverPosition;
		clearDragState();
		if (from === null || position === null) return;
		closeSwitch();
		// A reorder can leave the strength textbox mid-edit pointed at a row
		// that is no longer the one it was opened for - the each-block itself
		// stays correctly bound to its row via the (row.model) key below, but
		// `editingIndex` is a plain index into `rows` and has no way to know
		// its row just moved.
		editingIndex = null;
		emit(moveItem(rows, from, dropIndexFor(from, index, position)));
	}

	/** Arrow Up/Down on the grip handle moves the row by one position -
	 * keyboard-operable reordering without a separate pair of buttons. */
	function handleGripKeydown(event: KeyboardEvent, index: number) {
		if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
		event.preventDefault();
		editingIndex = null;
		closeSwitch();
		const target = event.key === 'ArrowUp' ? index - 1 : index + 1;
		emit(moveItem(rows, index, target));
	}

	function updateStrength(index: number, strength: number) {
		emit(rows.map((r, i) => (i === index ? { model: r.model, strength } : r)));
	}

	// The row's enabled/disabled state is ONLY ever flipped here (via
	// `isLoraRowDisabled`/`toggleLoraStrength` in loraStrength.ts) - never
	// derived from the live strength value, so dragging the slider through
	// (or resting exactly on) zero never disables the row. The remembered
	// strength to restore rides on the row itself (`saved_strength`) rather
	// than in component state, so it survives a page reload via the Session
	// feature - see loraStrength.ts for the full trace of why that's safe.
	function toggleRowEnabled(index: number) {
		emit(rows.map((r, i) => (i === index ? toggleLoraStrength(r, strengthDefault) : r)));
	}

	function nudgeRowStrength(index: number, direction: 1 | -1, event: MouseEvent) {
		const row = rows[index];
		const next = nudgeStrength(row.strength, direction, {
			large: event.shiftKey,
			min: strengthMin,
			max: strengthMax
		});
		updateStrength(index, next);
	}

	// --- Inline editable strength value (next to the slider) ---
	let editingIndex: number | null = null;
	let editText = '';

	function focusStrengthEdit(index: number, current: number) {
		editingIndex = index;
		editText = current.toFixed(2);
	}

	function inputStrengthEdit(event: Event) {
		editText = (event.target as HTMLInputElement).value;
	}

	function commitStrengthEdit(index: number) {
		if (editingIndex !== index) return;
		editingIndex = null;
		// Clamped to min/max only - never rounded to strengthStep, so an exact
		// typed value (e.g. 0.04 on a 0.1-step slider) always survives.
		const parsed = parseStrengthInput(editText, strengthMin, strengthMax);
		if (parsed === null) return; // non-numeric input reverts (no mutation)
		updateStrength(index, parsed);
	}

	function handleStrengthEditKeydown(event: KeyboardEvent, index: number) {
		if (event.key === 'Enter') {
			(event.target as HTMLInputElement).blur();
		} else if (event.key === 'Escape') {
			editingIndex = null; // revert - blur below will no-op since editingIndex no longer matches
			(event.target as HTMLInputElement).blur();
		}
	}

	function handleOpenDetails(rowModelRef: string) {
		const model = resolveModel(rowModelRef);
		if (!allowInfoModal || !model?.id) return;
		modalModelId = model.id;
		isModelDetailsOpen = true;
	}

	async function handleCloseDetails() {
		isModelDetailsOpen = false;
		const closedId = modalModelId;
		modalModelId = null;
		// Refresh the edited model so freshly-added triggers show up immediately.
		if (closedId) {
			try {
				const response = await api.getModelById(closedId, true);
				if (response.success && response.data?.model) {
					const updated = response.data.model as Model;
					models = models.map((m) => (m.id === updated.id ? updated : m));
				}
			} catch (error) {
				logger.error('[LoraPickerField] Failed to refresh model after details close:', error);
			}
		}
	}

	async function handleTriggerClick(trigger: string) {
		const ok = await copyText(trigger);
		toasts.info(ok ? 'Copied to clipboard' : 'Could not copy');
	}


	function toggleFavoritesOnly() {
		favoritesOnly = !favoritesOnly;
		fetchLibrary();
	}

	function displayName(model?: Model, fallbackPath?: string): string {
		const name = modelDisplayName(model);
		if (name) return name;
		// Never expose an internal stable id while its metadata is hydrating (or
		// if the referenced model has since been removed from the library).
		if (fallbackPath?.startsWith(MODEL_REF_PREFIX)) return 'Selected model';
		return (fallbackPath || '').split('/').pop() || '';
	}

	function thumbnailFor(model?: Model): string | undefined {
		if (!model) return undefined;
		const files = filesWithPreview(model);
		if (files.length === 0) return undefined;
		const imageFile = files.find((f: any) => f.file_type === 'image');
		const fallbackFile = files.find((f: any) => f.thumbnail_small);
		return imageFile?.thumbnail_small || fallbackFile?.thumbnail_small;
	}

	function filenameStem(model?: Model): string {
		return modelFilenameStem(model);
	}

	function summaryParts(model?: Model): string[] {
		return modelSummaryParts(model);
	}

	function modelPurpose(model?: Model): string {
		return modelTypePresentation(model?.model_type).purpose;
	}

	// --- Tag filter chip handlers ---
	function openTagInput() {
		showTagInput = true;
		requestAnimationFrame(() => tagInputRef?.focus());
	}

	function addTagFilter() {
		const trimmed = newTagQuery.trim();
		if (trimmed && !tagFilters.includes(trimmed)) {
			tagFilters = [...tagFilters, trimmed];
			tagFiltersSeeded = true;
		}
		newTagQuery = '';
		showTagInput = false;
	}

	function removeTagFilter(index: number) {
		tagFilters = tagFilters.filter((_, i) => i !== index);
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
</script>

<svelte:window on:pointerup={disarmDrag} />

<div class="field-card">
	<div class="flex items-center justify-between mb-2">
		<div class="flex items-baseline gap-1.5">
			<label class="label !mb-0" for={name || undefined}>{label}</label>
			{#if maxItems != null}
				<span class="font-mono text-2xs tabular-nums text-fg-subtle">{rows.length} / {maxItems}</span>
			{/if}
		</div>
		{#if rows.length > 0}
			<Button
				size="xs"
				variant="secondary"
				icon="plus"
				disabled={atMaxItems || showSearch}
				onclick={openSearch}
			>
				Add LoRA
			</Button>
		{/if}
	</div>

	{#if description}
		<p id={name ? `${name}-desc` : undefined} class="text-xs text-fg-muted mb-2">{description}</p>
	{/if}

	<!-- Tag + favorites filters for the add-search - sticky while the field stays mounted.
	     Inert (but harmless) while the add panel is showing its Collections view, which
	     doesn't consult tagFilters/favoritesOnly - see ModelBrowserPanel.svelte. -->
	{#if allowTagFilters}
		<div class="flex flex-wrap items-center gap-1.5 mb-2">
			<span class="inline-flex items-center gap-1 text-2xs text-fg-subtle uppercase tracking-wide">
				<Icon name="search" className="w-3 h-3" />
				Filter
			</span>
			<button
				type="button"
				class="inline-flex items-center gap-1 px-1.5 py-0.5 text-2xs rounded border transition-colors {favoritesOnly
					? 'bg-warning/10 text-warning border-warning/25'
					: 'text-fg-muted border-line-strong hover:text-fg'}"
				title={favoritesOnly ? 'Showing favorites only' : 'Show favorites only'}
				aria-pressed={favoritesOnly}
				on:click={toggleFavoritesOnly}
			>
				<Icon name="star" className="w-2.5 h-2.5" />
				Favorites
			</button>
			{#each tagFilters as tag, index}
				<span
					class="inline-flex items-center gap-1 px-1.5 py-0.5 text-2xs rounded border bg-signal/10 text-signal border-signal/25"
				>
					{tag}
					<button
						type="button"
						class="hover:opacity-70"
						title="Remove filter"
						on:click={() => removeTagFilter(index)}
					>
						<Icon name="close" className="w-2.5 h-2.5" />
					</button>
				</span>
			{/each}
			{#if showTagInput}
				<input
					type="text"
					autocomplete="off"
					bind:this={tagInputRef}
					bind:value={newTagQuery}
					on:keydown={handleTagInputKeydown}
					on:blur={addTagFilter}
					placeholder="Tag name..."
					class="input text-2xs py-0.5 px-1.5 w-24"
				/>
			{:else}
				<button
					type="button"
					class="text-2xs text-fg-muted hover:text-fg px-1"
					on:click={openTagInput}
				>
					+ Tag
				</button>
			{/if}
		</div>
	{/if}

	{#if rows.length === 0 && !showSearch}
		<!-- Empty state -->
		<div class="flex flex-col items-center justify-center gap-2 py-6 bg-surface-2 border border-dashed border-line-strong rounded-lg text-center">
			<div class="w-10 h-10 rounded-full bg-surface-3 flex items-center justify-center text-fg-subtle">
				<Icon name="image" className="w-5 h-5" />
			</div>
			<p class="text-sm text-fg-muted">No LoRAs added</p>
			<Button size="sm" variant="secondary" icon="plus" disabled={atMaxItems} onclick={openSearch}>
				Add LoRA
			</Button>
		</div>
	{:else}
		<div class="flex flex-col gap-2">
			{#each rows as row, index (rowKeys[index])}
				{@const model = rowModels[index]}
				{@const triggers = model ? effectiveTriggerWords(model) : []}
				{@const thumbnailUrl = thumbnailFor(model)}
				{@const enabled = !isLoraRowDisabled(row)}
				<div
					class="relative bg-surface-2 rounded-lg p-2.5 transition-opacity {draggingIndex === index
						? 'opacity-50'
						: !enabled
							? 'opacity-60'
							: ''} {dragOverIndex === index && dragOverPosition === 'before'
						? 'border border-line border-t-2 border-t-signal'
						: dragOverIndex === index && dragOverPosition === 'after'
							? 'border border-line border-b-2 border-b-signal'
							: 'border border-line'}"
					role="listitem"
					draggable={armedIndex === index}
					on:dragstart={(e) => handleRowDragStart(e, index)}
					on:dragend={clearDragState}
					on:dragover={(e) => handleRowDragOver(e, index)}
					on:dragleave={() => handleRowDragLeave(index)}
					on:drop={(e) => handleRowDrop(e, index)}
				>
					<div class="flex gap-3">
						<!-- Thumbnail -->
						{#if thumbnailUrl}
							<img
								src={thumbnailUrl}
								alt={displayName(model, row.model)}
								class="shrink-0 w-14 h-14 object-cover rounded"
								loading="lazy"
							/>
						{:else}
							<div
								class="shrink-0 w-14 h-14 bg-surface-3 rounded flex items-center justify-center text-fg-subtle"
								style={placeholderTint(displayName(model, row.model))}
							>
								<Icon name="image" className="w-6 h-6" />
							</div>
						{/if}

						<div class="flex-1 min-w-0">
								<div class="flex items-start justify-between gap-2">
									<div class="min-w-0">
										<div class="text-sm font-medium text-fg truncate" title={displayName(model, row.model)}>
											{displayName(model, row.model)}
										</div>
										{#if model && filenameStem(model)}
											<div class="truncate font-mono text-2xs text-fg-subtle" title={model.filename}>
												File · {filenameStem(model)}
											</div>
										{/if}
										{#if model && suggestedStrength(model) !== undefined}
											<div class="font-mono text-2xs tabular-nums text-fg-subtle">
												Suggested {formatStrength(suggestedStrength(model) ?? strengthDefault)}
											</div>
										{/if}
										{#if model}
											<div class="mt-1 flex min-w-0 items-center gap-1 overflow-hidden">
												{#each summaryParts(model) as part, partIndex}
													<span
														class="max-w-[9rem] truncate rounded bg-surface-3 px-1.5 py-0.5 text-2xs text-fg-muted"
														title={partIndex === 0 ? modelPurpose(model) : part}
													>
														{part}
													</span>
												{/each}
											</div>
										{/if}
									</div>
								<div class="flex items-center gap-0.5 shrink-0">
									<!-- Drag handle - moved off the row's left edge (was crowding thumbnail/content
									     width); the top-right corner cluster already reserves space for row controls. -->
									<Tooltip text="Drag to reorder" position="top">
										<button
											type="button"
											class="inline-flex items-center justify-center rounded p-1.5 text-fg-subtle opacity-40 transition-all hover:opacity-100 hover:text-fg hover:bg-surface-3/50 cursor-grab active:cursor-grabbing touch-none"
											aria-label={`Reorder ${displayName(model, row.model)} - drag, or press Arrow Up/Down`}
											on:pointerdown={() => armDrag(index)}
											on:keydown={(e) => handleGripKeydown(e, index)}
										>
											<Icon name="grip" className="w-4 h-4" />
										</button>
									</Tooltip>
									{#if model}
										<button
											type="button"
											class="inline-flex items-center justify-center rounded p-1.5 transition-colors {model.is_favorite
												? 'text-warning hover:bg-surface-3/50'
												: 'text-fg-muted hover:text-fg hover:bg-surface-3/50'}"
											aria-label={model.is_favorite ? 'Remove from favorites' : 'Add to favorites'}
											title={model.is_favorite ? 'Remove from favorites' : 'Add to favorites'}
											on:click={(e) => toggleModelFavorite(model, e)}
										>
											<Icon name="star" className="w-4 h-4" />
										</button>
									{/if}
									<IconButton
										icon="refresh"
										label="Switch LoRA model"
										size="sm"
										active={switchingIndex === index}
										onclick={() => openSwitch(index)}
									/>
									{#if allowInfoModal && model}
										<IconButton
											icon="info"
											label="View LoRA details"
											size="sm"
											onclick={() => handleOpenDetails(row.model)}
										/>
									{/if}
									<IconButton icon="close" label="Remove LoRA" size="sm" onclick={() => removeRow(index)} />
								</div>
							</div>

							<!-- Strength -->
							<div class="flex items-center gap-1.5 mt-2">
								<Tooltip text={enabled ? 'Disable (strength → 0)' : 'Enable (restore strength)'} position="top">
									<Switch
										size="sm"
										checked={enabled}
										label={enabled ? 'Disable LoRA' : 'Enable LoRA'}
										onchange={() => toggleRowEnabled(index)}
									/>
								</Tooltip>
								<input
									type="range"
									class="flex-1 h-2 bg-surface-3 rounded-lg appearance-none cursor-pointer accent-signal disabled:cursor-not-allowed"
									min={strengthMin}
									max={strengthMax}
									step={strengthStep}
									value={row.strength}
									disabled={!enabled}
									on:input={(e) =>
										updateStrength(index, parseFloat((e.target as HTMLInputElement).value))}
								/>
								<div class="flex items-center gap-px shrink-0">
									<button
										type="button"
										class="inline-flex items-center justify-center h-5 w-5 rounded text-fg-subtle hover:text-fg hover:bg-surface-3 disabled:pointer-events-none transition-colors"
										aria-label="Decrease strength"
										title="−0.05 (Shift: −0.25)"
										disabled={!enabled}
										on:click={(e) => nudgeRowStrength(index, -1, e)}
									>
										<Icon name="minus" className="w-3 h-3" />
									</button>
									<input
										type="text"
										inputmode="decimal"
										autocomplete="off"
										class="w-12 shrink-0 text-right text-xs font-mono tabular-nums text-fg bg-surface-3/40 border border-line rounded px-1 py-0.5 hover:border-line-hover focus:outline-none focus:border-line-strong focus:bg-surface-3 transition-colors disabled:pointer-events-none"
										value={editingIndex === index ? editText : formatStrength(row.strength)}
										aria-label="LoRA strength value"
										disabled={!enabled}
										on:focus={() => focusStrengthEdit(index, row.strength)}
										on:input={inputStrengthEdit}
										on:blur={() => commitStrengthEdit(index)}
										on:keydown={(e) => handleStrengthEditKeydown(e, index)}
									/>
									<button
										type="button"
										class="inline-flex items-center justify-center h-5 w-5 rounded text-fg-subtle hover:text-fg hover:bg-surface-3 disabled:pointer-events-none transition-colors"
										aria-label="Increase strength"
										title="+0.05 (Shift: +0.25)"
										disabled={!enabled}
										on:click={(e) => nudgeRowStrength(index, 1, e)}
									>
										<Icon name="plus" className="w-3 h-3" />
									</button>
								</div>
							</div>

							<!-- Trigger chips -->
							{#if showTriggers && triggers.length > 0}
								<div class="flex flex-wrap gap-1 mt-2">
									{#each triggers as trigger}
										{@const isActive = hasTriggerWordMatch(activePromptText, trigger)}
										<button
											type="button"
											class="px-1.5 py-0.5 text-2xs rounded border transition-colors {isActive
												? 'bg-signal/10 text-signal border-signal/25'
												: 'bg-surface-3 text-fg-muted border-line-strong hover:text-fg hover:border-line-hover'}"
											title={isActive ? 'Already in prompt' : `Copy "${trigger}"`}
											on:click={() => handleTriggerClick(trigger)}
										>
											{trigger}
										</button>
									{/each}
								</div>
							{/if}
						</div>
					</div>

					{#if switchingIndex === index}
						{@const switchExcludeIds = new Set([...selectedModelIds].filter((id) => id !== model?.id))}
						<div class="mt-2 bg-surface-1 border border-line-strong rounded-lg shadow-floating overflow-hidden">
							<div class="flex items-center gap-2 p-2 border-b border-line">
								<Icon name="search" className="w-4 h-4 text-fg-subtle shrink-0" />
								<input
									type="text"
									autocomplete="off"
									bind:value={switchSearchQuery}
									on:keydown={handleSwitchSearchKeydown}
									placeholder="Search LoRAs..."
									class="input flex-1 text-sm py-1"
								/>
								<IconButton icon="close" label="Close switch" size="sm" onclick={closeSwitch} />
							</div>
							<div class="max-h-72 overflow-y-auto">
								<ModelBrowserPanel
									{modelType}
									{presetId}
									limit={100}
									searchQuery={switchSearchQuery}
									{tagFilters}
									{filterTagIds}
									{favoritesOnly}
									excludeIds={switchExcludeIds}
									rowSize="sm"
									onSelect={(picked) => switchRowModel(index, picked)}
								/>
							</div>
						</div>
					{/if}
				</div>
			{/each}

			{#if !showSearch && rows.length > 0 && !atMaxItems}
				<button
					type="button"
					class="flex items-center justify-center gap-1.5 py-2 text-xs text-fg-muted border border-dashed border-line-strong rounded-lg hover:text-fg hover:border-line-hover transition-colors"
					on:click={openSearch}
				>
					<Icon name="plus" className="w-3.5 h-3.5" />
					Add another LoRA
				</button>
			{/if}
		</div>
	{/if}

	<!-- Add / search panel -->
	{#if showSearch}
		<div class="mt-2 bg-surface-1 border border-line-strong rounded-lg shadow-floating overflow-hidden">
			<div class="flex items-center gap-2 p-2 border-b border-line">
				<Icon name="search" className="w-4 h-4 text-fg-subtle shrink-0" />
				<input
					bind:this={searchInputRef}
					type="text"
					autocomplete="off"
					bind:value={searchQuery}
					on:keydown={handleSearchKeydown}
					placeholder={fieldConfig.placeholder || 'Search LoRAs...'}
					class="input flex-1 text-sm py-1"
				/>
				<IconButton icon="close" label="Close search" size="sm" onclick={closeSearch} />
			</div>

			<div class="max-h-72 overflow-y-auto">
				<ModelBrowserPanel
					{modelType}
					{presetId}
					limit={100}
					{searchQuery}
					{tagFilters}
					{filterTagIds}
					{favoritesOnly}
					excludeIds={selectedModelIds}
					rowSize="sm"
					onSelect={selectModel}
				/>
			</div>
		</div>
	{/if}
</div>

<ModelDetailsModal isOpen={isModelDetailsOpen} modelId={modalModelId} onClose={handleCloseDetails} />
