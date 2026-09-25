<script lang="ts">
	import { createEventDispatcher, tick } from 'svelte';
	import { api } from '$lib/services/api';
	import type { Prompt, SegmentTemplate } from '$lib/types/segments';
	import { flattenRichSegments, type SegmentApplyMode } from '$lib/utils/richSegments';
	import type { PresetSegmentTemplate } from '$lib/utils/presetSegmentTemplates';
	import { logger } from '$lib/utils/logger';
	import { modelDisplayName } from '$lib/utils/modelDisplay';
	import { parseVariableUsageTokens } from '$lib/utils/promptVariables';
	import { timeAgo } from '$lib/utils/relativeTime';
	import BaseModal from './BaseModal.svelte';
	import ConfirmFooter from './ConfirmFooter.svelte';
	import ConfirmModal from './ConfirmModal.svelte';
	import ModelAssignmentModal from './ModelAssignmentModal.svelte';
	import { createConfirmSettlementGate, getConfirmKeyboardAction, settleIfEligible } from './confirmKeyboard';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Badge, Button, Spinner, Alert } from '$lib/components/ui';
	import PromptFiltersPopover from '$lib/prompts/PromptFiltersPopover.svelte';
	import {
		DEFAULT_PROMPT_FILTERS,
		promptFilterActiveCount,
		promptFilterChips,
		clearPromptFilterChip,
		clearAllPromptFilters,
		type PromptFilters,
		type PromptSortBy
	} from '$lib/prompts/promptFilters';
	import { queryPromptLibrary } from '$lib/prompts/promptLibraryQuery';
	import { promptCardTitle, summarizePromptVariables } from '$lib/prompts/promptCardDisplay';

	export let isOpen = false;
	export let kind: 'prompt' | 'template' = 'prompt';
	export let targetHasMeaningfulContent = false;
	export let presetTemplates: PresetSegmentTemplate[] = [];

	type LibraryItem = Prompt | SegmentTemplate | PresetSegmentTemplate;

	const dispatch = createEventDispatcher<{
		close: void;
		apply: { item: LibraryItem; mode: SegmentApplyMode };
	}>();

	const PAGE_SIZE = 48;
	const SORT_OPTIONS: ReadonlyArray<{ value: PromptSortBy; label: string }> = [
		{ value: 'last_used_at', label: 'Last used' },
		{ value: 'created_at', label: 'Created' },
		{ value: 'name', label: 'Name' },
		{ value: 'usage_count', label: 'Most used' }
	];

	let loadedItems: LibraryItem[] = [];
	let total = 0;
	let loading = false;
	let loadingMore = false;
	let error = '';
	let searchTerm = '';
	let selectedId: string | null = null;
	let applyMode: SegmentApplyMode | null = null;
	let previousOpen = false;
	let showReplaceConfirmation = false;
	const applyModes: SegmentApplyMode[] = ['append', 'prepend', 'replace'];

	let filters: PromptFilters = { ...DEFAULT_PROMPT_FILTERS };
	let filtersOpen = false;
	let showModelPicker = false;
	let semanticHitCount = 0;
	let promptRequestId = 0;
	let loadDebounce: ReturnType<typeof setTimeout> | undefined;
	let models: Array<{ id: string; filename?: string; name?: string; model_type?: string }> = [];
	let modelsLoaded = false;
	let bodyEl: HTMLDivElement | undefined;
	let listEl: HTMLDivElement | undefined;
	let filtersTriggerEl: HTMLDivElement | undefined;
	const settlementGate = createConfirmSettlementGate();

	$: items = kind === 'template' ? [...presetTemplates, ...loadedItems] : loadedItems;
	$: normalizedSearch = searchTerm.trim().toLowerCase();
	$: visibleItems =
		kind === 'template'
			? items.filter((item) => {
					if (!normalizedSearch) return true;
					return [itemName(item), itemPreview(item), itemDescription(item), ...(item.tags || [])]
						.join(' ')
						.toLowerCase()
						.includes(normalizedSearch);
				})
			: items;
	$: selectedItem = visibleItems.find((item) => item.id === selectedId) || null;
	$: hasMore = kind === 'prompt' && loadedItems.length < total;
	$: filterCount = promptFilterActiveCount(filters);
	$: modelLabel = filters.modelId
		? modelDisplayName(models.find((model) => model.id === filters.modelId)) || 'Selected model'
		: 'Any';
	$: filterChips = promptFilterChips(filters, modelLabel !== 'Any' ? modelLabel : undefined);
	$: searchHint = semanticHitCount > 0 ? `semantic · ${semanticHitCount} hits` : null;
	$: emptyReason = filters.q.trim() || filterCount > 0 ? 'No prompts match.' : 'No prompts yet.';

	$: if (isOpen !== previousOpen) {
		previousOpen = isOpen;
		if (isOpen) {
			resetSelection();
			resetLibrary();
			loadItems();
		}
	}

	function itemName(item: LibraryItem): string {
		if (kind === 'prompt') {
			const prompt = item as Prompt;
			return prompt.name || prompt.display_name || itemPreview(item) || 'Untitled Prompt';
		}
		return item.name || 'Untitled Segment Template';
	}

	function itemPreview(item: LibraryItem): string {
		if ('flattened_text' in item && item.flattened_text) return item.flattened_text;
		return flattenRichSegments(item.segments);
	}

	function itemDescription(item: LibraryItem): string {
		return 'description' in item ? item.description || '' : '';
	}

	function isPresetItem(item: LibraryItem): boolean {
		return 'origin' in item && item.origin === 'preset';
	}

	function promptSourceLabel(prompt: Prompt): string | null {
		return prompt.source_provider && prompt.source_provider !== 'manual' ? prompt.source_provider : null;
	}

	function promptUsageLabel(prompt: Prompt): string {
		return prompt.usage_count ? `used ${prompt.usage_count}×` : 'never used';
	}

	function promptRelativeLabel(prompt: Prompt): string {
		const when = prompt.last_used_at || prompt.created_at;
		return when ? timeAgo(when) : '';
	}

	function resetSelection() {
		searchTerm = '';
		selectedId = null;
		applyMode = null;
		showReplaceConfirmation = false;
		settlementGate.reset();
	}

	function resetLibrary() {
		clearTimeout(loadDebounce);
		filters = { ...DEFAULT_PROMPT_FILTERS };
		filtersOpen = false;
		showModelPicker = false;
		semanticHitCount = 0;
		total = 0;
		error = '';
	}

	function loadItems() {
		if (kind === 'prompt') {
			if (!modelsLoaded) void loadModels();
			void loadPrompts(true);
		} else {
			void loadTemplates();
		}
	}

	async function loadModels() {
		modelsLoaded = true;
		try {
			const response = await api.getModels({ limit: 200, sort_by: 'filename', sort_order: 'asc' });
			models = response.data?.models || [];
		} catch {
			models = [];
		}
	}

	async function loadTemplates() {
		loading = true;
		error = '';
		try {
			const response = await api.listSegmentTemplates();
			if (!response.success) throw new Error(response.error || 'Failed to load Segment Templates');
			loadedItems = response.data?.templates || [];
		} catch (loadError) {
			logger.error('Failed to load template library:', loadError);
			error = loadError instanceof Error ? loadError.message : 'Failed to load template library';
			loadedItems = [];
		} finally {
			loading = false;
		}
	}

	async function loadPrompts(reset: boolean) {
		const requestId = ++promptRequestId;
		if (reset) loading = true;
		else loadingMore = true;
		error = '';
		try {
			const result = await queryPromptLibrary(api, filters, {
				limit: PAGE_SIZE,
				offset: reset ? 0 : loadedItems.length
			});
			if (requestId !== promptRequestId) return;
			loadedItems = reset ? result.rows : [...loadedItems, ...result.rows];
			total = result.total;
			semanticHitCount = reset ? result.semanticHits : semanticHitCount;
		} catch (loadError) {
			if (requestId !== promptRequestId) return;
			logger.error('Failed to load prompt library:', loadError);
			error = loadError instanceof Error ? loadError.message : 'Failed to load prompt library';
			if (reset) loadedItems = [];
		} finally {
			if (requestId === promptRequestId) {
				loading = false;
				loadingMore = false;
			}
		}
	}

	function updateFilters(next: PromptFilters) {
		filters = next;
		clearTimeout(loadDebounce);
		loadDebounce = setTimeout(() => void loadPrompts(true), 250);
	}

	function handleSearchInput() {
		if (kind !== 'prompt') return;
		updateFilters({ ...filters, q: searchTerm });
	}

	function handleSortChange(event: Event) {
		updateFilters({ ...filters, sortBy: (event.currentTarget as HTMLSelectElement).value as PromptSortBy });
	}

	function removeChip(key: string) {
		updateFilters(clearPromptFilterChip(filters, key));
	}

	function clearFilters() {
		updateFilters(clearAllPromptFilters(filters));
	}

	function selectModelForFilter(model: { id: string } | null) {
		showModelPicker = false;
		if (model && !models.some((entry) => entry.id === model.id)) models = [model as never, ...models];
		updateFilters({ ...filters, modelId: model?.id || '' });
	}

	function ownDialog(): Element | null {
		return bodyEl?.closest('[role="dialog"]') ?? null;
	}

	function handleWindowClick(event: MouseEvent) {
		if (!filtersOpen) return;
		const target = event.target as Element | null;
		if (!target || filtersTriggerEl?.contains(target)) return;
		const dialog = target.closest('[role="dialog"], [role="alertdialog"], [aria-label="Close modal"]');
		if (dialog && dialog !== ownDialog()) return;
		filtersOpen = false;
	}

	function moveSelection(step: 1 | -1) {
		if (!visibleItems.length) return;
		const index = visibleItems.findIndex((item) => item.id === selectedId);
		const next = index < 0 ? 0 : Math.min(Math.max(index + step, 0), visibleItems.length - 1);
		selectedId = visibleItems[next].id;
		void tick().then(() => {
			const row = listEl?.querySelector('[aria-selected="true"]') as HTMLElement | null;
			row?.scrollIntoView?.({ block: 'nearest' });
		});
	}

	function handleListKeydown(event: KeyboardEvent) {
		if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
			event.preventDefault();
			moveSelection(event.key === 'ArrowDown' ? 1 : -1);
		} else if (event.key === 'Enter') {
			if (!selectedItem || !applyMode) return;
			event.preventDefault();
			handleConfirm();
		}
	}

	function handleClose() {
		resetSelection();
		clearTimeout(loadDebounce);
		filtersOpen = false;
		showModelPicker = false;
		dispatch('close');
	}

	function handleCancel() {
		settlementGate.settle(handleClose);
	}

	function handleConfirm() {
		settleIfEligible(settlementGate, !!selectedItem && !!applyMode, requestApply);
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		if (showModelPicker) return;
		if (filtersOpen) {
			if (event.key === 'Escape') {
				event.preventDefault();
				filtersOpen = false;
			}
			return;
		}
		const { action, suppress } = getConfirmKeyboardAction(event);
		if (action === 'cancel') handleCancel();
		else if (action === 'confirm') handleConfirm();
		if (suppress) event.preventDefault();
	}

	function requestApply() {
		if (!selectedItem || !applyMode) return;
		if (applyMode === 'replace' && targetHasMeaningfulContent) {
			showReplaceConfirmation = true;
			return;
		}
		applySelection();
	}

	function applySelection() {
		if (!selectedItem || !applyMode) return;
		dispatch('apply', { item: selectedItem, mode: applyMode });
		resetSelection();
	}
</script>

<svelte:window on:click|capture={handleWindowClick} on:keydown|capture={handleWindowKeydown} />

<BaseModal
	{isOpen}
	title={kind === 'prompt' ? 'Apply Prompt' : 'Apply Segment Template'}
	sizeClass={kind === 'prompt' ? 'md:max-w-5xl md:w-full md:max-h-[88vh]' : 'md:max-w-3xl md:w-full md:max-h-[85vh]'}
	handleEscapeKey={false}
	on:close={handleCancel}
>
	<svelte:fragment slot="headerIcon">
		<Icon name={kind === 'prompt' ? 'book-open' : 'layout-template'} className="h-5 w-5 text-fg-muted" />
	</svelte:fragment>

	<div class="flex flex-col gap-4 p-4 sm:p-6" bind:this={bodyEl}>
		{#if kind === 'prompt'}
			<div class="flex flex-wrap items-center gap-2">
				<div class="input flex h-8 min-w-[14rem] flex-1 items-center gap-2">
					<Icon name="search" className="h-3.5 w-3.5 flex-shrink-0 text-fg-subtle" />
					<input
						type="search"
						class="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-fg-subtle"
						placeholder="Search prompts…"
						aria-label="Search prompts"
						bind:value={searchTerm}
						on:input={handleSearchInput}
						on:keydown={handleListKeydown}
					/>
					{#if loading && loadedItems.length > 0}
						<Spinner size="sm" />
					{:else if searchHint}
						<span class="whitespace-nowrap font-mono text-xs tabular-nums text-fg-subtle">{searchHint}</span>
					{/if}
				</div>

				<div class="relative flex-shrink-0" bind:this={filtersTriggerEl}>
					<button
						type="button"
						class="input flex h-8 items-center gap-1.5 whitespace-nowrap text-xs font-medium"
						aria-haspopup="dialog"
						aria-expanded={filtersOpen}
						on:click={() => (filtersOpen = !filtersOpen)}
					>
						<span>Filters</span>
						{#if filterCount > 0}
							<span class="font-mono tabular-nums text-signal">{filterCount}</span>
						{/if}
						<Icon name="chevron-down" className="h-3 w-3 text-fg-subtle" />
					</button>
					{#if filtersOpen}
						<div class="absolute right-0 top-[calc(100%+6px)] z-40">
							<PromptFiltersPopover
								{filters}
								{modelLabel}
								onChange={updateFilters}
								onOpenModelPicker={() => (showModelPicker = true)}
								onClose={() => (filtersOpen = false)}
							/>
						</div>
					{/if}
				</div>

				<label class="relative flex-shrink-0">
					<span class="sr-only">Sort</span>
					<select class="input h-8 appearance-none pr-6 text-xs font-medium" value={filters.sortBy} on:change={handleSortChange}>
						{#each SORT_OPTIONS as option (option.value)}
							<option value={option.value}>{option.label}</option>
						{/each}
					</select>
				</label>
			</div>

			{#if filterChips.length > 0}
				<div class="flex flex-wrap items-center gap-1.5">
					{#each filterChips as chip (chip.key)}
						<span class="inline-flex h-6 items-center gap-1.5 rounded border border-signal/28 bg-signal/10 px-2 font-mono text-xs text-signal">
							{chip.label}
							<button
								type="button"
								aria-label={`Remove filter ${chip.label}`}
								class="opacity-70 hover:opacity-100"
								on:click={() => removeChip(chip.key)}
							>
								<Icon name="close" className="h-2.5 w-2.5" />
							</button>
						</span>
					{/each}
					<button type="button" class="text-xs text-fg-subtle hover:text-fg" on:click={clearFilters}>Clear all</button>
					<span class="ml-auto font-mono text-xs tabular-nums text-fg-subtle">{loadedItems.length} of {total}</span>
				</div>
			{/if}
		{:else}
			<div>
				<label for="segment-library-search" class="mb-1.5 block text-xs font-medium text-fg-muted">
					Search Segment Templates
				</label>
				<input
					id="segment-library-search"
					type="search"
					class="input w-full"
					bind:value={searchTerm}
					placeholder="Search template names and content…"
					on:keydown={handleListKeydown}
				/>
			</div>
		{/if}

		{#if loading && loadedItems.length === 0}
			<div class="flex justify-center py-12"><Spinner size="lg" /></div>
		{:else if error}
			<Alert variant="danger" live="polite">{error}</Alert>
		{:else if visibleItems.length === 0}
			<div class="flex flex-col items-center gap-3 rounded-lg border border-dashed border-line p-8 text-center text-sm text-fg-muted">
				{#if kind === 'prompt'}
					<span>{emptyReason}</span>
					{#if filterCount > 0}
						<Button size="xs" variant="secondary" onclick={clearFilters}>Clear filters</Button>
					{/if}
				{:else}
					<span>No Segment Templates found.</span>
				{/if}
			</div>
		{:else}
			<div
				class="space-y-2 overflow-y-auto pr-1 {kind === 'prompt' ? 'h-[48vh] min-h-[16rem]' : 'max-h-[46vh]'}"
				role="listbox"
				tabindex="-1"
				aria-label={`${kind} library`}
				bind:this={listEl}
				on:keydown={handleListKeydown}
			>
				{#each visibleItems as item (item.id)}
					<button
						type="button"
						class="w-full rounded-lg border text-left transition-colors {kind === 'prompt' ? 'px-3 py-2.5' : 'p-3'} {selectedId === item.id
							? 'border-signal bg-signal/10'
							: 'border-line bg-surface-2 hover:border-line-hover hover:bg-surface-3'}"
						role="option"
						aria-selected={selectedId === item.id}
						data-library-row
						on:click={() => (selectedId = item.id)}
					>
						{#if kind === 'prompt'}
							{@const prompt = item as Prompt}
							{@const title = promptCardTitle(prompt)}
							{@const negative = prompt.usage_hint === 'negative'}
							{@const variablesSummary = summarizePromptVariables(prompt.variables)}
							{@const sourceLabel = promptSourceLabel(prompt)}
							{@const relativeLabel = promptRelativeLabel(prompt)}
							<div class="flex items-start gap-2.5">
								<Tooltip text={negative ? 'Negative prompt' : 'Positive prompt'}>
									<span
										class="mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded border font-mono text-xs {negative
											? 'border-danger/35 text-danger'
											: 'border-line-strong bg-surface-1 text-fg-muted'}"
									>
										{negative ? '−' : '+'}
									</span>
								</Tooltip>
								<div class="flex min-w-0 flex-1 flex-col gap-1">
									<span
										class="min-w-0 truncate text-sm font-semibold {title.untitled
											? 'font-medium italic text-fg-muted'
											: 'text-fg'}"
									>
										{title.text}
									</span>
									<p class="line-clamp-2 text-sm leading-relaxed text-fg-muted">
										{#each parseVariableUsageTokens(prompt.flattened_text || '') as token, index (index)}
											{#if token.type === 'variable'}<span class="font-mono text-xs text-signal">{token.raw}</span
												>{:else}{token.raw}{/if}
										{/each}
									</p>
									<div class="flex flex-wrap items-center gap-1.5">
										<span
											class="inline-flex h-5 max-w-full items-center truncate rounded border border-line-strong bg-surface-1 px-1.5 font-mono text-xs text-fg-muted"
										>
											{prompt.model_name || 'any model'}
										</span>
										{#if sourceLabel}
											<span class="inline-flex h-5 items-center rounded border border-line-strong bg-surface-1 px-1.5 font-mono text-xs text-fg-muted">
												{sourceLabel}
											</span>
										{/if}
										{#if variablesSummary.count > 0}
											<Tooltip text={variablesSummary.broken ? 'A condition references a missing variable' : 'Variables used in this prompt'}>
												<span
													class="inline-flex h-5 items-center gap-1 rounded border px-1.5 font-mono text-xs {variablesSummary.broken
														? 'border-warning/35 bg-warning/10 text-warning'
														: 'border-signal/28 bg-signal/10 text-signal'}"
												>
													<Icon name="braces" className="h-2.5 w-2.5" />
													{variablesSummary.count}{variablesSummary.linked > 0 ? ` · ${variablesSummary.linked} linked` : ''}
												</span>
											</Tooltip>
										{/if}
										{#if prompt.nsfw}
											<span class="inline-flex h-5 items-center rounded border border-line-strong bg-surface-1 px-1.5 font-mono text-xs text-fg-muted">
												nsfw
											</span>
										{/if}
										<span class="ml-auto flex items-center gap-2.5 whitespace-nowrap font-mono text-xs tabular-nums text-fg-subtle">
											<span>{promptUsageLabel(prompt)}</span>
											{#if relativeLabel}<span>{relativeLabel}</span>{/if}
										</span>
									</div>
								</div>
							</div>
						{:else}
							<div class="flex items-start justify-between gap-3">
								<div class="min-w-0 flex-1">
									<div class="flex items-center gap-2">
										<span class="truncate text-sm font-medium text-fg">{itemName(item)}</span>
										{#if isPresetItem(item)}
											<Badge size="sm" class="flex-shrink-0">Preset</Badge>
										{/if}
									</div>
									<div class="mt-1 line-clamp-2 text-xs leading-relaxed text-fg-muted">
										{itemPreview(item) || 'Blank starter segments'}
									</div>
								</div>
								<span class="flex-shrink-0 font-mono text-xs text-fg-subtle">
									{item.segments.length} segment{item.segments.length === 1 ? '' : 's'}
								</span>
							</div>
						{/if}
					</button>
				{/each}
				{#if hasMore}
					<div class="flex justify-center pt-1">
						<Button size="sm" variant="secondary" loading={loadingMore} disabled={loadingMore} onclick={() => loadPrompts(false)}>
							Show {Math.min(PAGE_SIZE, total - loadedItems.length)} more
						</Button>
					</div>
				{/if}
			</div>
		{/if}

		<fieldset>
			<legend class="mb-2 text-xs font-medium text-fg-muted">Choose how to apply</legend>
			<div class="grid grid-cols-3 gap-2">
				{#each applyModes as mode}
					<label
						class="cursor-pointer rounded-lg border p-2.5 text-center text-xs font-medium capitalize transition-colors {applyMode === mode
							? 'border-signal bg-signal/10 text-fg'
							: 'border-line text-fg-muted hover:border-line-hover'}"
					>
						<input class="sr-only" type="radio" name="segment-apply-mode" value={mode} bind:group={applyMode} />
						{mode}
					</label>
				{/each}
			</div>
			<p class="mt-2 text-xs text-fg-subtle">
				Incoming segments are detached copies. Other generation settings are not changed.
			</p>
		</fieldset>
	</div>

	<svelte:fragment slot="footer">
		<ConfirmFooter
			confirmLabel="Apply"
			confirmDisabled={!selectedItem || !applyMode}
			onCancel={handleCancel}
			onConfirm={handleConfirm}
		/>
	</svelte:fragment>
</BaseModal>

{#if showModelPicker}
	<ModelAssignmentModal
		selectionMode="single"
		selectedModelId={filters.modelId || null}
		allowClear={true}
		title="Filter prompts by model"
		subtitle="Search the model catalog or narrow it by type, then select one model."
		onSelect={selectModelForFilter}
		onClear={() => selectModelForFilter(null)}
		onClose={() => (showModelPicker = false)}
	/>
{/if}

<ConfirmModal
	isOpen={showReplaceConfirmation}
	title="Replace current segments?"
	message="This will replace the meaningful content in this editor. Preset, mode, form values, session, backend, seed, tags, and generation settings stay unchanged."
	variant="warning"
	on:confirm={() => {
		showReplaceConfirmation = false;
		applySelection();
	}}
	on:cancel={() => (showReplaceConfirmation = false)}
/>
