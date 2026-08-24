<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { createEventDispatcher, onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import Icon from '$lib/components/Icon.svelte';
	import SearchableMultiSelectPopover from '$lib/components/selectors/SearchableMultiSelectPopover.svelte';
	import { filterBySearch } from '$lib/components/selectors/searchFilter';

	const CREATE_OPTION_ID = '__create__';

	// Props
	export let selectedTagIds: string[] = [];
	export let placeholder: string = 'Add tags...';
	export let allowCreate: boolean = true;
	export let className: string = '';
	export let compact: boolean = false;
	export let iconOnly: boolean = false; // shorthand for triggerStyle="icon"
	/** How the closed trigger renders. 'pills' shows the current selection inline, replacing the
	 * icon/text button - this is the former AutoTagSelector presentation. */
	export let triggerStyle: 'button' | 'icon' | 'pills' = iconOnly ? 'icon' : 'button';
	/** 'remove-on-select' (default) pulls a picked tag out of the list into a chips row above it.
	 * 'checklist' leaves it in place with an inline checkmark instead - the former AutoTagSelector
	 * selection model, better suited to picking from a small fixed set. */
	export let listStyle: 'remove-on-select' | 'checklist' = 'remove-on-select';
	/** Defers the tag fetch until the popover first opens, instead of loading on mount. */
	export let loadOnOpen: boolean = false;
	export let openUpward: boolean = false;
	export let tagType: 'GENERATION' | 'MODEL' | 'UPLOAD' = 'GENERATION'; // Tag type to load and create

	const dispatch = createEventDispatcher<{
		change: string[];
	}>();

	interface Tag {
		id: string;
		name: string;
		type: string;
		usage_count?: number;
	}

	let availableTags: Tag[] = [];
	let searchQuery: string = '';
	let isOpen: boolean = false;
	let isLoading: boolean = false;
	let isCreating: boolean = false;
	let tagsLoaded: boolean = false;

	async function loadTags() {
		if (tagsLoaded) return;
		isLoading = true;
		try {
			const response = await api.getTags(tagType);
			if (response.success && response.data?.tags) {
				availableTags = response.data.tags;
			}
		} catch (error) {
			logger.error('[TagSelector] Error loading tags:', error);
		} finally {
			isLoading = false;
			tagsLoaded = true;
		}
	}

	onMount(() => {
		if (!loadOnOpen) loadTags();
	});

	function handlePopoverOpen() {
		if (loadOnOpen) loadTags();
	}

	$: tagNameMap = new Map(availableTags.map((t) => [t.id, t.name]));

	// Resolves selected ids to names once loaded; falls back to an ellipsis (not the raw id) while
	// tags are still in flight so trigger pills don't flash a UUID before the fetch settles.
	$: selectedTags = selectedTagIds.map((id) => ({
		id,
		name: tagNameMap.get(id) ?? (tagsLoaded ? id : '…')
	}));

	// Filter tags based on search query
	$: searchMatches = filterBySearch(availableTags, searchQuery, (tag) => tag.name);

	// 'remove-on-select' pulls a selected tag out of the browsable list (it's shown as a chip
	// instead); 'checklist' keeps every tag listed and marks selection with a checkmark.
	$: listedTags =
		listStyle === 'remove-on-select'
			? searchMatches.filter((tag) => !selectedTagIds.includes(tag.id))
			: searchMatches;

	// Check if search query matches existing tag exactly
	$: exactMatch = availableTags.find(
		(tag) => tag.name.toLowerCase() === searchQuery.toLowerCase().trim()
	);

	// Show create button if query doesn't match any existing tag and allowCreate is true
	$: showCreateButton =
		allowCreate && searchQuery.trim() && !exactMatch && !selectedTagIds.includes(searchQuery);

	$: optionIds = [
		...(showCreateButton ? [CREATE_OPTION_ID] : []),
		...listedTags.map((tag) => tag.id)
	];

	function addTag(tagId: string) {
		if (!selectedTagIds.includes(tagId)) {
			const newSelectedIds = [...selectedTagIds, tagId];
			selectedTagIds = newSelectedIds;
			dispatch('change', newSelectedIds);
		}
		if (listStyle === 'remove-on-select') {
			searchQuery = '';
		}
	}

	function removeTag(tagId: string, event?: MouseEvent) {
		event?.stopPropagation();
		const newSelectedIds = selectedTagIds.filter((id) => id !== tagId);
		selectedTagIds = newSelectedIds;
		dispatch('change', newSelectedIds);
	}

	function toggleTag(tagId: string) {
		if (selectedTagIds.includes(tagId)) {
			removeTag(tagId);
		} else {
			addTag(tagId);
		}
	}

	async function createTag() {
		const tagName = searchQuery.trim();
		if (!tagName) return;

		isCreating = true;
		try {
			const response = await api.createTag(tagName, tagType);
			if (response.success && response.data?.tag) {
				const newTag = response.data.tag;
				availableTags = [...availableTags, newTag];
				addTag(newTag.id);
			}
		} catch (error) {
			logger.error('[TagSelector] Error creating tag:', error);
		} finally {
			isCreating = false;
		}
	}

	// Fired by the popover's own listbox keyboard navigation (Enter/Space on the highlighted
	// option) as well as every option button's on:click below.
	function selectOption(id: string) {
		if (id === CREATE_OPTION_ID) {
			createTag();
			return;
		}
		if (listStyle === 'checklist') {
			toggleTag(id);
		} else {
			addTag(id);
		}
	}
</script>

<SearchableMultiSelectPopover
	bind:open={isOpen}
	bind:searchValue={searchQuery}
	placement={openUpward ? 'up' : 'down'}
	align={triggerStyle === 'icon' ? 'right' : 'left'}
	panelClass={compact ? 'w-72 max-h-96' : 'w-80 max-h-96'}
	searchPlaceholder={allowCreate ? 'Search or create tags...' : 'Search tags...'}
	{optionIds}
	onOpen={handlePopoverOpen}
	onSelect={selectOption}
>
	{#snippet trigger({ open, toggle })}
		{#if triggerStyle === 'icon'}
			<button
				type="button"
				on:click={toggle}
				class="{className} bg-black/50 hover:bg-black/70 text-white p-3 rounded-lg shadow-lg backdrop-blur-sm transition-colors relative"
				aria-label="Manage tags"
				aria-expanded={open}
				aria-haspopup="listbox"
			>
				<Icon name="tag" className="h-6 w-6" />
				{#if selectedTags.length > 0}
					<span
						class="absolute -top-1 -right-1 bg-signal-solid text-white text-xs font-semibold font-mono tabular-nums rounded h-5 min-w-5 px-1 flex items-center justify-center"
					>
						{selectedTags.length}
					</span>
				{/if}
			</button>
		{:else if triggerStyle === 'pills'}
			<div
				class="{className} inline-flex items-center gap-1 px-2 py-1 min-h-[26px] min-w-[90px] bg-surface-2 border border-line-strong rounded cursor-pointer select-none text-xs text-fg transition-colors hover:bg-surface-3 hover:border-line-hover"
				role="button"
				tabindex="0"
				on:click|stopPropagation={toggle}
				on:keydown={(e) => {
					if (e.key === 'Enter' || e.key === ' ') {
						e.preventDefault();
						toggle();
					}
				}}
				aria-expanded={open}
				aria-haspopup="listbox"
			>
				{#if selectedTags.length > 0}
					<div class="flex flex-wrap gap-1 flex-1">
						{#each selectedTags as tag (tag.id)}
							<span
								class="inline-flex items-center gap-1 px-1.5 py-0.5 bg-surface-3 border border-line-strong rounded text-[0.7rem] text-fg-muted whitespace-nowrap"
							>
								{tag.name}
								<button
									type="button"
									class="text-fg-subtle hover:text-danger transition-colors"
									on:click|stopPropagation={(e) => removeTag(tag.id, e)}
									aria-label={`Remove ${tag.name}`}
								>
									×
								</button>
							</span>
						{/each}
					</div>
				{:else}
					<span class="flex-1 text-fg-subtle">{placeholder}</span>
				{/if}
				<Icon
					name="chevron-down"
					className="h-3.5 w-3.5 flex-shrink-0 text-fg-subtle transition-transform {open
						? 'rotate-180'
						: ''}"
				/>
			</div>
		{:else}
			<button
				type="button"
				on:click={toggle}
				class="{className} inline-flex items-center gap-2 px-3 py-2 bg-surface-2 border border-line-strong rounded-lg shadow-sm hover:bg-surface-3 transition-colors text-sm font-medium text-fg focus:outline-none focus:ring-2 focus:ring-accent focus:border-transparent"
				aria-expanded={open}
				aria-haspopup="listbox"
			>
				<Icon name="tag" className="w-4 h-4" />
				<span>
					{#if selectedTags.length === 0}
						{placeholder}
					{:else}
						{selectedTags.length} {selectedTags.length === 1 ? 'tag' : 'tags'}
					{/if}
				</span>
				<Icon
					name="chevron-down"
					className="w-4 h-4 transition-transform {open ? 'rotate-180' : ''}"
				/>
			</button>
		{/if}
	{/snippet}

	{#snippet panel({ activeId, optionId, listboxId })}
		<!-- Selected tags -->
		{#if listStyle === 'remove-on-select' && selectedTags.length > 0}
			<div class="px-3 py-2 border-b border-line">
				<div class="flex flex-wrap gap-1.5">
					{#each selectedTags as tag (tag.id)}
						<button
							type="button"
							on:click={(e) => removeTag(tag.id, e)}
							class="inline-flex items-center gap-1 px-2 py-1 bg-signal/10 text-signal border border-signal/25 rounded text-xs font-medium hover:bg-signal/20 transition-colors"
						>
							<span>{tag.name}</span>
							<Icon name="close" className="w-3 h-3" />
						</button>
					{/each}
				</div>
			</div>
		{/if}

		<!-- Tags list -->
		<div
			id={listboxId}
			class="overflow-y-auto flex-1"
			role="listbox"
			aria-label="Available tags"
			aria-multiselectable="true"
		>
			{#if isLoading}
				<div class="px-3 py-8 text-center text-sm text-fg-muted">
					<div class="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-signal"></div>
					<div class="mt-2">Loading tags...</div>
				</div>
			{:else if listedTags.length === 0 && !showCreateButton}
				<div class="px-3 py-8 text-center text-sm text-fg-muted">
					{searchQuery ? 'No tags found' : 'No tags available'}
				</div>
			{:else}
				<div class="py-1">
					<!-- Create new tag option -->
					{#if showCreateButton}
						<button
							type="button"
							id={optionId(CREATE_OPTION_ID)}
							on:click={createTag}
							disabled={isCreating}
							class="w-full px-3 py-2 text-left text-sm hover:bg-surface-3 transition-colors flex items-center gap-2 text-info font-medium border-b border-line {activeId ===
							CREATE_OPTION_ID
								? 'bg-surface-3'
								: ''}"
							role="option"
							aria-selected="false"
						>
							<Icon name="plus" className="w-4 h-4 flex-shrink-0" />
							<span class="flex-1">
								{isCreating ? 'Creating...' : `Create "${searchQuery}"`}
							</span>
						</button>
					{/if}

					<!-- Available tags list -->
					{#each listedTags as tag (tag.id)}
						<button
							type="button"
							id={optionId(tag.id)}
							on:click={() => selectOption(tag.id)}
							class="w-full px-3 py-2 text-left text-sm text-fg hover:bg-surface-3 transition-colors flex items-center gap-2 group {activeId ===
							tag.id
								? 'bg-surface-3'
								: ''}"
							role="option"
							aria-selected={selectedTagIds.includes(tag.id)}
						>
							{#if listStyle === 'checklist'}
								<span
									class="flex h-3.5 w-3.5 flex-shrink-0 items-center justify-center rounded-sm border border-line-strong text-[0.6rem] {selectedTagIds.includes(
										tag.id
									)
										? 'border-signal-solid bg-signal-solid text-white'
										: ''}"
								>
									{#if selectedTagIds.includes(tag.id)}✓{/if}
								</span>
							{/if}
							<span class="flex-1 truncate">{tag.name}</span>
							{#if listStyle === 'remove-on-select' && tag.usage_count !== undefined && tag.usage_count > 0}
								<span class="text-xs text-fg-subtle">({tag.usage_count})</span>
							{/if}
							{#if listStyle === 'remove-on-select'}
								<Icon
									name="plus"
									className="w-4 h-4 text-fg-muted opacity-0 group-hover:opacity-100 transition-opacity"
								/>
							{/if}
						</button>
					{/each}
				</div>
			{/if}
		</div>

		<!-- Footer with tag count -->
		{#if listStyle === 'remove-on-select' && (selectedTags.length > 0 || availableTags.length > 0)}
			<div class="px-3 py-2 border-t border-line bg-surface-1 text-xs text-fg-muted">
				{selectedTags.length} selected • {availableTags.length} total
			</div>
		{/if}
	{/snippet}
</SearchableMultiSelectPopover>
