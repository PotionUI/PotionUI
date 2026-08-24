<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import GenerationCard from '$lib/components/GenerationCard.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import MediaPickerFrame from './MediaPickerFrame.svelte';
	import { Badge, Button, Spinner, Pagination } from '$lib/components/ui';
	import type { GenerationFile, GenerationHistoryItem, Tag } from '$lib/types/history';
	import { filterFilesByMediaType } from './generationHistoryMediaFilter';
	import { buildHistoryFilterParams } from './generationHistoryFilterParams';
	import { historyCollectionsStore as collectionsStore } from '$lib/stores/collections';
	import { buildTree, flattenTree } from '$lib/components/pane';
	import { filterBySearch } from '$lib/components/selectors/searchFilter';
	import SearchableMultiSelectPopover from '$lib/components/selectors/SearchableMultiSelectPopover.svelte';

	// Props
	export let isOpen: boolean = false;
	export let onClose: () => void;
	export let onSelect: (generation: GenerationHistoryItem, file: GenerationFile) => void;
	export let mediaType: 'image' | 'video' | undefined = undefined;
	export let title: string = 'Select Media from Generation History';

	// State
	let generationHistory: GenerationHistoryItem[] = [];
	let isLoadingHistory = false;
	let totalGenerations = 0;
	let currentPage = 1;
	let pageSize = 20;

	// Filter states
	type DatePreset = 'today' | 'yesterday' | 'last_week' | 'last_month' | 'all' | 'custom';
	let datePreset: DatePreset = 'all';
	let dateFilterFrom: string | null = null;
	let dateFilterTo: string | null = null;
	let customFromDate = '';
	let customToDate = '';
	let showCustomDatePopover = false;

	// Tag filter states
	let availableTags: Tag[] = [];
	let selectedTagIds: string[] = [];
	let tagSearchValue = '';
	let isTagDropdownOpen = false;
	let tagDropdownRef: HTMLDivElement;

	// Collection filter state. Single-select (the backend takes one
	// `collection_id`) - `null` means "All collections" and is never sent as
	// a param at all, see generationHistoryFilterParams.ts.
	let selectedCollectionId: string | null = null;
	let collectionSearchValue = '';
	let isCollectionPopoverOpen = false;
	let collectionsLoaded = false;

	const ALL_COLLECTIONS_OPTION_ID = '__all__';

	$: collections = $collectionsStore.collections;
	$: flattenedCollections = flattenTree(buildTree(collections));
	$: filteredCollectionNodes = filterBySearch(
		flattenedCollections,
		collectionSearchValue,
		(node) => node.item.name
	);
	$: collectionOptionIds = [
		ALL_COLLECTIONS_OPTION_ID,
		...filteredCollectionNodes.map((node) => node.item.id)
	];
	$: selectedCollectionName = selectedCollectionId
		? (collections.find((c) => c.id === selectedCollectionId)?.name ?? '…')
		: null;

	async function handleCollectionPopoverOpen() {
		if (collectionsLoaded) return;
		collectionsLoaded = true;
		await collectionsStore.load();
	}

	function selectCollection(id: string) {
		selectedCollectionId = id === ALL_COLLECTIONS_OPTION_ID ? null : id;
		isCollectionPopoverOpen = false;
	}

	// Format date helper
	function formatDate(date: Date): string {
		const year = date.getFullYear();
		const month = String(date.getMonth() + 1).padStart(2, '0');
		const day = String(date.getDate()).padStart(2, '0');
		return `${year}-${month}-${day}`;
	}

	// Get date range for preset
	function getDateRangeForPreset(preset: DatePreset): { from: string | null; to: string | null } {
		const today = new Date();
		today.setHours(0, 0, 0, 0);

		switch (preset) {
			case 'today':
				return { from: formatDate(today), to: formatDate(today) };
			case 'yesterday':
				const yesterday = new Date(today);
				yesterday.setDate(yesterday.getDate() - 1);
				return { from: formatDate(yesterday), to: formatDate(yesterday) };
			case 'last_week':
				const weekAgo = new Date(today);
				weekAgo.setDate(weekAgo.getDate() - 7);
				return { from: formatDate(weekAgo), to: formatDate(today) };
			case 'last_month':
				const monthAgo = new Date(today);
				monthAgo.setMonth(monthAgo.getMonth() - 1);
				return { from: formatDate(monthAgo), to: formatDate(today) };
			case 'all':
			default:
				return { from: null, to: null };
		}
	}

	// Load tags
	async function loadTags() {
		try {
			const response = await api.getTags('GENERATION');
			if (response.success && response.data) {
				availableTags = response.data.tags || [];
			}
		} catch (error) {
			logger.error('Failed to load tags:', error);
		}
	}

	// Load generation history
	async function loadGenerationHistory(page: number = 1) {
		isLoadingHistory = true;
		currentPage = page;

		try {
			const params: NonNullable<Parameters<typeof api.getGenerationHistory>[0]> = {
				limit: pageSize,
				offset: (page - 1) * pageSize,
				status: 'completed',
				mediaType: mediaType,
				includeTags: true
			};

			if (dateFilterFrom) params.createdFrom = dateFilterFrom;
			if (dateFilterTo) params.createdTo = dateFilterTo;
			Object.assign(
				params,
				buildHistoryFilterParams({ tagIds: selectedTagIds, collectionId: selectedCollectionId })
			);

			const response = await api.getGenerationHistory(params);

			if (response.success && response.data) {
				generationHistory = response.data.generations || [];
				totalGenerations = response.data.total || 0;
			}
		} catch (error) {
			logger.error('Failed to load generation history:', error);
		} finally {
			isLoadingHistory = false;
		}
	}

	// Handle date preset change
	function handleDatePresetChange(preset: DatePreset) {
		datePreset = preset;
		if (preset !== 'custom') {
			const range = getDateRangeForPreset(preset);
			dateFilterFrom = range.from;
			dateFilterTo = range.to;
			loadGenerationHistory(1);
		}
	}

	// Apply custom date filter
	function applyCustomDateFilter() {
		if (customFromDate || customToDate) {
			dateFilterFrom = customFromDate || null;
			dateFilterTo = customToDate || null;
			datePreset = 'custom';
			showCustomDatePopover = false;
			loadGenerationHistory(1);
		}
	}

	// Handle file selection from generation. The card is fed a media-type-filtered
	// COPY of the generation, so the unfiltered original is what gets forwarded -
	// a consumer resolving provenance needs the real `files` array.
	function handleSelectFromGeneration(generation: GenerationHistoryItem, file: GenerationFile | null) {
		if (!file) return;
		onSelect(generation, file);
	}

	// Handle click outside to close tag dropdown
	function handleClickOutside(event: MouseEvent) {
		if (tagDropdownRef && !tagDropdownRef.contains(event.target as Node)) {
			isTagDropdownOpen = false;
		}
	}

	// Watch for modal open
	$: if (isOpen) {
		loadTags();
		loadGenerationHistory(1);
		document.addEventListener('mousedown', handleClickOutside);
	} else {
		document.removeEventListener('mousedown', handleClickOutside);
	}

	// Watch for tag filter changes
	$: if (isOpen && selectedTagIds) {
		loadGenerationHistory(1);
	}

	// Watch for collection filter changes
	$: if (isOpen && selectedCollectionId !== undefined) {
		loadGenerationHistory(1);
	}

	// Get total pages
	$: totalPages = Math.ceil(totalGenerations / pageSize);

	// Watch for page size changes
	$: if (isOpen && pageSize) {
		loadGenerationHistory(1);
	}

	// Get media type label
	$: mediaTypeLabel = mediaType === 'image' ? 'Image' : mediaType === 'video' ? 'Video' : 'Media';
</script>

<MediaPickerFrame
	{isOpen}
	{onClose}
	{title}
	subtitle="Browse and select {mediaTypeLabel.toLowerCase()}s from your previous generations"
>
	<svelte:fragment slot="header">
		{#if totalGenerations > 0}
			<Badge variant="neutral" class="hidden md:inline-flex font-mono tabular-nums">
				{totalGenerations} generations
			</Badge>
		{/if}

		<!-- Date Filter Controls -->
		<div class="w-full space-y-2 md:space-y-3">
			<div class="flex items-center gap-2">
				<span class="hidden md:inline text-sm text-fg-muted">Quick filters:</span>
				<div class="flex gap-1.5 md:gap-2 overflow-x-auto no-scrollbar">
					{#each [
						{ id: 'all', label: 'All' },
						{ id: 'today', label: 'Today' },
						{ id: 'yesterday', label: 'Yesterday' },
						{ id: 'last_week', label: '7 Days' },
						{ id: 'last_month', label: '30 Days' }
					] as preset}
						<button
							type="button"
							class="flex-shrink-0 px-2.5 md:px-3 py-1 md:py-1.5 text-xs md:text-sm font-medium rounded transition-colors {datePreset === preset.id
								? 'bg-signal/10 text-signal'
								: 'bg-surface-2 text-fg-muted hover:bg-surface-3'}"
							on:click={() => handleDatePresetChange(preset.id as DatePreset)}
						>
							{preset.label}
						</button>
					{/each}

					<!-- Custom Date Range Popover -->
					<div class="relative flex-shrink-0">
						<button
							type="button"
							class="px-2.5 md:px-3 py-1 md:py-1.5 text-xs md:text-sm font-medium rounded transition-colors flex items-center gap-1 {datePreset === 'custom'
								? 'bg-signal/10 text-signal'
								: 'bg-surface-2 text-fg-muted hover:bg-surface-3'}"
							on:click={() => showCustomDatePopover = !showCustomDatePopover}
						>
							<Icon name="calendar" className="w-3.5 h-3.5 md:w-4 md:h-4" />
							<span class="hidden md:inline">Custom Range</span>
							<span class="md:hidden">Custom</span>
						</button>

						{#if showCustomDatePopover}
							<div class="fixed md:absolute inset-x-3 top-auto bottom-20 md:inset-x-auto md:bottom-auto md:top-full md:left-0 md:mt-1 bg-surface-1 border border-line-strong rounded-lg shadow-floating p-4 z-50 md:w-64">
								<h4 class="text-sm font-medium text-fg mb-3">Custom Date Range</h4>
								<div class="space-y-3">
									<div>
										<label class="block text-xs text-fg-muted mb-1" for="custom-date-from">From</label>
										<input
											id="custom-date-from"
											type="date"
											class="input w-full text-sm"
											bind:value={customFromDate}
										/>
									</div>
									<div>
										<label class="block text-xs text-fg-muted mb-1" for="custom-date-to">To</label>
										<input
											id="custom-date-to"
											type="date"
											class="input w-full text-sm"
											bind:value={customToDate}
										/>
									</div>
									<div class="flex gap-2">
										<Button variant="secondary" class="flex-1" onclick={() => showCustomDatePopover = false}>
											Cancel
										</Button>
										<Button variant="primary" class="flex-1" onclick={applyCustomDateFilter}>
											Apply
										</Button>
									</div>
								</div>
							</div>
						{/if}
					</div>
				</div>
			</div>

			{#if dateFilterFrom || dateFilterTo}
				<div class="flex items-center gap-2 text-xs text-fg-muted">
					<Icon name="calendar" className="w-4 h-4 flex-shrink-0" />
					<span class="truncate">From <strong class="text-fg">{dateFilterFrom || 'start'}</strong> to <strong class="text-fg">{dateFilterTo || 'today'}</strong></span>
				</div>
			{/if}

			<!-- Tag Filter -->
			<div class="flex items-center gap-2">
				<div class="relative flex-1" bind:this={tagDropdownRef}>
					<div class="flex items-center gap-1.5 md:gap-2 flex-wrap">
						{#if selectedTagIds.length > 0}
							<div class="flex items-center gap-1 flex-wrap">
								{#each selectedTagIds as tagId}
									{@const tag = availableTags.find(t => t.id === tagId)}
									{#if tag}
										<span
											class="inline-flex items-center gap-1 px-2 py-0.5 md:py-1 text-xs font-medium rounded border"
											style="background-color: {tag.color}20; border-color: {tag.color}; color: {tag.color}"
										>
											{tag.name}
											<button
												type="button"
												class="hover:opacity-70 p-0.5"
												on:click={() => {
													selectedTagIds = selectedTagIds.filter(id => id !== tagId);
												}}
											>
												<Icon name="close" className="w-3 h-3" />
											</button>
										</span>
									{/if}
								{/each}
							</div>
						{/if}
						<button
							type="button"
							class="px-2.5 md:px-3 py-1 md:py-1.5 text-xs md:text-sm font-medium bg-surface-2 text-fg-muted hover:bg-surface-3 rounded flex items-center gap-1"
							on:click={() => isTagDropdownOpen = !isTagDropdownOpen}
						>
							<Icon name="tag" className="w-3.5 h-3.5 md:w-4 md:h-4" />
							{selectedTagIds.length > 0 ? `${selectedTagIds.length} tags` : 'Tags'}
						</button>
						{#if selectedTagIds.length > 0}
							<button
								type="button"
								class="px-2 py-1 text-xs font-medium text-danger hover:bg-surface-3 rounded"
								on:click={() => selectedTagIds = []}
							>
								Clear
							</button>
						{/if}
					</div>

					<!-- Tag Dropdown -->
					{#if isTagDropdownOpen}
						<div class="absolute top-full left-0 mt-1 w-64 bg-surface-1 border border-line-strong rounded-lg shadow-floating z-50">
							<div class="p-2">
								<input
									type="text"
									placeholder="Search tags..."
									class="input w-full text-sm"
									bind:value={tagSearchValue}
								/>
							</div>
							<div class="max-h-64 overflow-y-auto">
								<div class="p-2 space-y-1">
									{#each availableTags.filter(tag => tag.name.toLowerCase().includes(tagSearchValue.toLowerCase())) as tag}
										<div
											class="flex items-center gap-2 p-2 rounded-lg cursor-pointer hover:bg-surface-3/50 transition-colors {selectedTagIds.includes(tag.id) ? 'bg-surface-2' : ''}"
											on:click={() => {
												selectedTagIds = selectedTagIds.includes(tag.id)
													? selectedTagIds.filter(id => id !== tag.id)
													: [...selectedTagIds, tag.id];
											}}
											role="button"
											tabindex="0"
											on:keydown={(e) => e.key === 'Enter' && e.currentTarget.click()}
										>
											<div
												class="w-3 h-3 rounded-full flex-shrink-0"
												style="background-color: {tag.color}"
											></div>
											<span class="text-sm text-fg flex-1">{tag.name}</span>
											{#if selectedTagIds.includes(tag.id)}
												<Icon name="check" className="w-4 h-4 text-signal" />
											{/if}
										</div>
									{/each}
									{#if availableTags.filter(tag => tag.name.toLowerCase().includes(tagSearchValue.toLowerCase())).length === 0}
										<div class="p-4 text-center text-sm text-fg-muted">
											No tags found
										</div>
									{/if}
								</div>
							</div>
						</div>
					{/if}
				</div>
			</div>

			<!-- Collection Filter -->
			<div class="flex items-center gap-2">
				<SearchableMultiSelectPopover
					bind:open={isCollectionPopoverOpen}
					bind:searchValue={collectionSearchValue}
					panelClass="w-64"
					searchPlaceholder="Search collections..."
					optionIds={collectionOptionIds}
					onOpen={handleCollectionPopoverOpen}
					onSelect={selectCollection}
				>
					{#snippet trigger({ open, toggle })}
						<div class="flex items-center gap-1.5 md:gap-2 flex-wrap">
							<button
								type="button"
								class="px-2.5 md:px-3 py-1 md:py-1.5 text-xs md:text-sm font-medium rounded flex items-center gap-1 max-w-[10rem] md:max-w-[14rem] {selectedCollectionId
									? 'bg-signal/10 text-signal'
									: 'bg-surface-2 text-fg-muted hover:bg-surface-3'}"
								on:click|stopPropagation={toggle}
								aria-haspopup="listbox"
								aria-expanded={open}
							>
								<Icon name="folder" className="w-3.5 h-3.5 md:w-4 md:h-4 flex-shrink-0" />
								<span class="truncate">{selectedCollectionName ?? 'Collection'}</span>
							</button>
							{#if selectedCollectionId}
								<button
									type="button"
									class="px-2 py-1 text-xs font-medium text-danger hover:bg-surface-3 rounded"
									on:click|stopPropagation={() => selectCollection(ALL_COLLECTIONS_OPTION_ID)}
								>
									Clear
								</button>
							{/if}
						</div>
					{/snippet}

					{#snippet panel({ activeId, optionId, listboxId })}
						<div id={listboxId} class="max-h-64 overflow-y-auto py-1" role="listbox" aria-label="Collections">
							<button
								type="button"
								id={optionId(ALL_COLLECTIONS_OPTION_ID)}
								class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors {!selectedCollectionId
									? 'bg-signal/10 text-signal'
									: 'text-fg hover:bg-surface-3/50'} {activeId === ALL_COLLECTIONS_OPTION_ID ? 'ring-1 ring-inset ring-signal/50' : ''}"
								on:click={() => selectCollection(ALL_COLLECTIONS_OPTION_ID)}
								role="option"
								aria-selected={!selectedCollectionId}
							>
								All collections
							</button>
							{#if $collectionsStore.loading && collections.length === 0}
								<p class="px-3 py-2 text-center text-xs text-fg-subtle">Loading collections…</p>
							{:else if filteredCollectionNodes.length === 0}
								<p class="px-3 py-2 text-center text-xs text-fg-subtle">
									{collections.length === 0 ? 'No collections yet' : 'No collections found'}
								</p>
							{:else}
								{#each filteredCollectionNodes as node (node.item.id)}
									<button
										type="button"
										id={optionId(node.item.id)}
										class="flex w-full items-center gap-2 py-1.5 pr-3 text-left text-sm transition-colors {selectedCollectionId === node.item.id
											? 'bg-signal/10 text-signal'
											: 'text-fg hover:bg-surface-3/50'} {activeId === node.item.id ? 'ring-1 ring-inset ring-signal/50' : ''}"
										style="padding-left: {0.75 + node.depth * 0.9}rem"
										on:click={() => selectCollection(node.item.id)}
										role="option"
										aria-selected={selectedCollectionId === node.item.id}
									>
										<Icon name="folder" className="w-3.5 h-3.5 flex-shrink-0" />
										<span class="truncate">{node.item.name}</span>
									</button>
								{/each}
							{/if}
						</div>
					{/snippet}
				</SearchableMultiSelectPopover>
			</div>
		</div>
	</svelte:fragment>

	<!-- Modal Body -->
	<div class="p-3 md:p-6">
		{#if isLoadingHistory}
			<div class="flex flex-col items-center justify-center py-12">
				<Spinner size="lg" />
				<p class="text-fg-muted mt-3">Loading generations...</p>
			</div>
		{:else if generationHistory.length === 0}
			<div class="text-center py-12">
				<Icon name="image" className="w-16 h-16 text-fg-subtle mx-auto mb-4" />
				<p class="text-fg-muted text-lg">No generations found</p>
				<p class="text-fg-subtle text-sm mt-1">Try adjusting your filters or create some generations first</p>
			</div>
		{:else}
			<div class="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4">
				{#each generationHistory as generation}
					{@const filteredFiles = filterFilesByMediaType(generation.files, mediaType)}
					{#if filteredFiles && filteredFiles.length > 0}
						<GenerationCard
							generation={{...generation, files: filteredFiles}}
							thumbnailSize="medium"
							selectable={true}
							selected={false}
							onSelect={(_gen, file) => handleSelectFromGeneration(generation, file)}
						/>
					{/if}
				{/each}
			</div>
		{/if}
	</div>

	<svelte:fragment slot="footer">
		<div class="p-3 md:p-4">
			<!-- Mobile footer: stacked layout -->
			<div class="flex flex-col gap-2 md:hidden">
				{#if totalPages > 1}
					<div class="text-center text-xs text-fg-muted font-mono tabular-nums">
						{((currentPage - 1) * pageSize) + 1}-{Math.min(currentPage * pageSize, totalGenerations)} of {totalGenerations}
					</div>
					<div class="flex justify-center overflow-x-auto">
						<Pagination
							{currentPage}
							{totalPages}
							size="sm"
							onPageChange={loadGenerationHistory}
						/>
					</div>
				{:else if totalGenerations > 0}
					<div class="text-center text-xs text-fg-muted font-mono tabular-nums">
						{totalGenerations} generation{totalGenerations !== 1 ? 's' : ''}
					</div>
				{/if}
			</div>

			<!-- Desktop footer: single row -->
			<div class="hidden md:flex items-center justify-between">
				<div class="flex items-center gap-4">
					{#if totalGenerations > 0}
						<span class="text-sm text-fg-muted font-mono tabular-nums">
							Showing {((currentPage - 1) * pageSize) + 1}-{Math.min(currentPage * pageSize, totalGenerations)} of {totalGenerations}
						</span>
					{/if}

					<div class="flex items-center gap-2">
						<label class="text-sm text-fg-muted" for="page-size-select">Items per page:</label>
						<select
							id="page-size-select"
							class="input text-sm w-auto"
							bind:value={pageSize}
							on:change={() => currentPage = 1}
						>
							<option value={10}>10</option>
							<option value={20}>20</option>
							<option value={50}>50</option>
							<option value={100}>100</option>
						</select>
					</div>

					<Pagination
						{currentPage}
						{totalPages}
						size="sm"
						onPageChange={loadGenerationHistory}
					/>
				</div>

				<button
					type="button"
					class="px-4 py-2 text-sm font-medium text-fg-muted hover:bg-surface-3/50 rounded transition-colors"
					on:click={onClose}
				>
					Cancel
				</button>
			</div>
		</div>
	</svelte:fragment>
</MediaPickerFrame>
