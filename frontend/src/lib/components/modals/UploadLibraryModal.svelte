<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { debounce } from '$lib/utils/time';
	import { onDestroy } from 'svelte';
	import { api } from '$lib/services/api/index';
	import { tagCatalog } from '$lib/stores/tagCatalog';
	import type { LibraryItem } from '$lib/services/api/library';
	import Icon from '$lib/components/Icon.svelte';
	import JustifiedLibraryGallery from '$lib/components/JustifiedLibraryGallery.svelte';
	import LibraryCard from '../../../routes/library/components/LibraryCard.svelte';
	import MediaPickerFrame from './MediaPickerFrame.svelte';
	import MediaPickerSelectionBar from './MediaPickerSelectionBar.svelte';
	import MediaPickPreviewPanel from './MediaPickPreviewPanel.svelte';
	import MediaPickConstraintNote from './MediaPickConstraintNote.svelte';
	import {
		createMediaSelection,
		isMediaTypeSelectable,
		mediaTypeConstraintMessage,
		pickedFromLibraryItem,
		type PickedMedia,
		type PickerMediaType
	} from './mediaPickerSelection';
	import QuickTagFilterBar from '$lib/components/QuickTagFilterBar.svelte';
	import { Badge, Button, IconButton, Spinner, EmptyState, Pagination, SegmentedControl } from '$lib/components/ui';
	import CollectionFilterPopover from '$lib/components/selectors/CollectionFilterPopover.svelte';
	import { libraryCollectionsStore } from '$lib/stores/collections';
	import { hasActiveLibraryFilters, type LibraryMediaTypeFilter } from '$lib/library/libraryQuery';
	import { buildLibraryQuery, DEFAULT_LIBRARY_FILTERS } from '$lib/library/libraryQuery';
	import { libraryItemDisplayName } from '$lib/library/libraryItemMeta';
	import { LIBRARY_TAG_TYPE } from '$lib/stores/library';
	import type { Tag } from '$lib/types/history';

	export let isOpen: boolean = false;
	export let onClose: () => void;
	export let onSelect: (item: LibraryItem) => void = () => {};
	export let mediaType: 'image' | 'video' | 'audio' | undefined = undefined;
	export let title: string = 'Select from Your Library';
	export let multiple: boolean = false;
	export let selectedKeys: string[] | undefined = undefined;
	export let onSelectionChange: ((keys: string[], items: PickedMedia[]) => void) | undefined = undefined;
	export let onConfirm: ((keys: string[], items: PickedMedia[]) => void) | undefined = undefined;
	export let selectableMediaTypes: PickerMediaType[] | undefined = undefined;

	const picker = createMediaSelection('library');
	const selection = picker.selection;
	let previewId: string | null = null;

	$: picker.follow(selectedKeys);
	$: picker.track(isOpen);
	$: if (!isOpen) previewId = null;
	$: constraintMessage = mediaTypeConstraintMessage(selectableMediaTypes);
	$: previewItem = previewId ? (items.find((i) => i.id === previewId) ?? null) : null;

	function handleCancel() {
		if (multiple) picker.cancel(onSelectionChange);
		onClose();
	}

	function handleConfirm() {
		if (picker.confirm(onConfirm)) onClose();
	}

	let items: LibraryItem[] = [];
	let availableTags: Tag[] = [];
	let selectedTagIds: string[] = [];
	let search = '';
	let kindFilter: LibraryMediaTypeFilter = 'all';
	let collectionId: string | null = null;
	let unsorted = false;
	let favoritesOnly = false;
	let collectionsLoaded = false;
	let isLoading = false;
	let loadError: string | null = null;
	let total = 0;
	let currentPage = 1;
	let pageSize = 20;

	let confirmingId: string | null = null;
	let deletingId: string | null = null;

	async function loadItems(page: number = 1) {
		isLoading = true;
		loadError = null;
		currentPage = page;

		try {
			const response = await api.listLibraryItems(
				buildLibraryQuery(
					{
						...DEFAULT_LIBRARY_FILTERS,
						mediaType: mediaType ?? kindFilter,
						selectedTagIds,
						collectionId: collectionId ?? undefined,
						unsorted,
						favoritesOnly,
						search
					},
					page,
					pageSize
				)
			);

			if (response.success && response.data) {
				items = response.data.items || [];
				if (multiple) picker.remember(items.map(pickedFromLibraryItem));
				total = response.data.total || 0;
			} else {
				loadError = 'Could not load your library.';
			}
		} catch (error) {
			logger.error('Failed to load library items:', error);
			loadError = 'Could not load your library.';
		} finally {
			isLoading = false;
		}
	}

	async function loadTags() {
		try {
			availableTags = await tagCatalog.get<Tag>(LIBRARY_TAG_TYPE, { fresh: true });
		} catch (error) {
			logger.error('Failed to load library tags:', error);
		}
	}

	$: if (isOpen) {
		confirmingId = null;
		loadItems(1);
		loadTags();
	}

	const scheduleSearch = debounce(() => loadItems(1), 300);
	function handleSearchInput(event: Event) {
		search = (event.currentTarget as HTMLInputElement).value;
		scheduleSearch();
	}

	onDestroy(scheduleSearch.cancel);

	function handleTagToggle(tagId: string) {
		selectedTagIds = selectedTagIds.includes(tagId)
			? selectedTagIds.filter((id) => id !== tagId)
			: [...selectedTagIds, tagId];
		loadItems(1);
	}

	function loadCollections() {
		if (collectionsLoaded) return;
		collectionsLoaded = true;
		void libraryCollectionsStore.load();
	}

	function handleCollectionChange(next: { collectionId: string | null; unsorted: boolean }) {
		collectionId = next.collectionId;
		unsorted = next.unsorted;
		loadItems(1);
	}

	function handleKindChange(id: string) {
		kindFilter = id as LibraryMediaTypeFilter;
		loadItems(1);
	}

	function toggleFavorites() {
		favoritesOnly = !favoritesOnly;
		loadItems(1);
	}

	$: filtering = hasActiveLibraryFilters({
		mediaType: mediaType ?? kindFilter,
		selectedTagIds,
		collectionId: collectionId ?? undefined,
		unsorted,
		favoritesOnly,
		search
	});

	function handleClearTags() {
		selectedTagIds = [];
		loadItems(1);
	}

	$: totalPages = Math.ceil(total / pageSize);
	$: mediaTypeLabel = mediaType ?? 'media';

	function requestDelete(itemId: string) {
		confirmingId = itemId;
	}

	function cancelDelete() {
		confirmingId = null;
	}

	async function confirmDelete(item: LibraryItem) {
		deletingId = item.id;
		try {
			const response = await api.deleteLibraryItem(item.id);
			if (response.success) {
				items = items.filter((i) => i.id !== item.id);
				total = Math.max(0, total - 1);
			} else {
				loadError = 'Could not delete that item.';
			}
		} catch (error) {
			logger.error('Failed to delete library item:', error);
			loadError = 'Could not delete that item.';
		} finally {
			deletingId = null;
			confirmingId = null;
		}
	}
</script>

<MediaPickerFrame
	{isOpen}
	onClose={multiple ? handleCancel : onClose}
	{title}
	subtitle="Everything in your library - uploads and copies you took from history"
>
	<svelte:fragment slot="header">
		{#if total > 0}
			<Badge variant="neutral" class="hidden md:inline-flex font-mono tabular-nums">
				{total} item{total !== 1 ? 's' : ''}
			</Badge>
		{/if}
	</svelte:fragment>

	<div class="p-3 md:p-6">
		<div class="relative mb-3 max-w-sm">
			<Icon
				name="search"
				className="w-3.5 h-3.5 text-fg-subtle absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none"
			/>
			<input
				type="text"
				class="input text-xs py-1.5 pl-8 pr-3 bg-surface-2/50 w-full"
				placeholder="Search by file name..."
				value={search}
				on:input={handleSearchInput}
			/>
		</div>

		<div class="mb-3 flex flex-wrap items-center gap-2" data-library-filters>
			{#if !mediaType}
				<SegmentedControl
					variant="toggle"
					ariaLabel="Kind"
					selected={kindFilter}
					onSelect={handleKindChange}
					items={[
						{ id: 'all', label: 'All' },
						{ id: 'image', label: 'Images' },
						{ id: 'video', label: 'Videos' },
						{ id: 'audio', label: 'Audio' }
					]}
				/>
			{/if}
			<CollectionFilterPopover
				collections={$libraryCollectionsStore.collections}
				loading={$libraryCollectionsStore.loading}
				{collectionId}
				{unsorted}
				offersUnsorted
				onOpen={loadCollections}
				onChange={handleCollectionChange}
			/>
			<button
				type="button"
				class="px-3 py-1.5 text-sm font-medium rounded flex items-center gap-1 {favoritesOnly ? 'bg-signal/10 text-signal' : 'bg-surface-2 text-fg-muted hover:bg-surface-3'}"
				aria-pressed={favoritesOnly}
				data-favorites-filter
				on:click={toggleFavorites}
			>
				<Icon name="star" className="w-4 h-4" />
				Favorites
			</button>
		</div>

		<QuickTagFilterBar
			tags={availableTags}
			selectedIds={selectedTagIds}
			onToggle={handleTagToggle}
			onClear={handleClearTags}
		/>

		{#if isLoading}
			<div class="flex flex-col items-center justify-center py-12">
				<Spinner size="lg" />
				<p class="text-fg-muted mt-3">Loading your library...</p>
			</div>
		{:else if loadError && items.length === 0}
			<EmptyState icon="photo" title="Something went wrong" description={loadError} />
		{:else if items.length === 0}
			<EmptyState
				icon="photo"
				title={filtering ? 'No matches' : 'Nothing here yet'}
				description={filtering ? 'Nothing in your library matches these filters.' : `Files you upload, and generations you copy across, show up in your library so you can reuse them.${mediaType ? ` Nothing ${mediaTypeLabel}-shaped is in it yet.` : ''}`}
			/>
		{:else}
			{#if loadError}
				<p class="text-sm text-danger mb-3">{loadError}</p>
			{/if}
			{#if multiple}
				<MediaPickConstraintNote message={constraintMessage} />
				{#if previewItem}
					<MediaPickPreviewPanel label={libraryItemDisplayName(previewItem)} onClose={() => (previewId = null)}>
						{#if previewItem.media_type === 'video'}
							<video src={previewItem.url} class="max-h-60 object-contain" controls>
								<track kind="captions" />
							</video>
						{:else if previewItem.media_type === 'image'}
							<img src={previewItem.url} alt={libraryItemDisplayName(previewItem)} class="max-h-60 object-contain" />
						{:else}
							<Icon name="audio" className="w-8 h-8 text-fg-muted" />
						{/if}
					</MediaPickPreviewPanel>
				{/if}
			{/if}
			<JustifiedLibraryGallery {items}>
				<svelte:fragment slot="card" let:item let:tile>
					{@const name = libraryItemDisplayName(item)}
					{@const allowed = !multiple || isMediaTypeSelectable(item.media_type, selectableMediaTypes)}
					{@const checked = $selection.includes(item.id)}
					<div
						class="flex flex-col min-w-0"
						style="width: {tile.width}px"
						data-testid="library-file-tile"
						data-item-id={item.id}
						data-selected={multiple ? checked : undefined}
					>
						<div
							class="group relative {allowed
								? '[&:hover_[data-library-card-body]>div]:border-line-hover'
								: 'opacity-50'}"
						>
							{#if multiple}
								<button
									type="button"
									class="block w-full rounded-lg text-left focus-visible:outline-none {allowed
										? 'cursor-pointer'
										: 'cursor-not-allowed'}"
									tabindex="-1"
									aria-hidden="true"
									disabled={!allowed}
									data-file={name}
									on:click={() => picker.toggle(item.id, onSelectionChange)}
								>
									<span class="pointer-events-none block" inert>
										<LibraryCard {item} {tile} showCheckbox={false} showActions={false} selected={checked} />
									</span>
								</button>
								<IconButton
									icon="search"
									label={`Preview ${name}`}
									size="sm"
									variant="overlay"
									class="absolute top-2 right-2 z-40 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100"
									onclick={(e) => {
										e.stopPropagation();
										previewId = item.id;
									}}
								/>
								<label
									class="absolute top-2 left-2 z-40 flex h-7 w-7 items-center justify-center rounded border border-line-strong bg-surface-1/90 {allowed
										? 'cursor-pointer'
										: 'cursor-not-allowed'}"
								>
									<input
										type="checkbox"
										class="h-4 w-4 accent-signal"
										{checked}
										disabled={!allowed}
										aria-label={`Select ${name}`}
										data-file={name}
										aria-describedby={!allowed && constraintMessage ? `library-pick-reason-${item.id}` : undefined}
										on:change={() => picker.toggle(item.id, onSelectionChange)}
									/>
								</label>
							{:else}
								<button
									type="button"
									class="block w-full rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-signal"
									aria-label={`Use ${name}`}
									data-file={name}
									on:click={() => onSelect(item)}
								>
									<span class="pointer-events-none block" inert>
										<LibraryCard {item} {tile} showCheckbox={false} showActions={false} />
									</span>
								</button>
								<div class="absolute top-2 right-2 z-40">
									{#if confirmingId === item.id}
										<div class="flex items-center gap-1 rounded-lg bg-surface-1 p-1 shadow-floating">
											<Button
												variant="danger"
												size="xs"
												disabled={deletingId === item.id}
												onclick={() => confirmDelete(item)}
											>
												{deletingId === item.id ? '...' : 'Delete'}
											</Button>
											<Button
												variant="secondary"
												size="xs"
												disabled={deletingId === item.id}
												onclick={cancelDelete}
											>
												Cancel
											</Button>
										</div>
									{:else}
										<IconButton
											icon="trash"
											label={`Delete ${name}`}
											size="sm"
											variant="overlay"
											class="opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100"
											onclick={() => requestDelete(item.id)}
										/>
									{/if}
								</div>
							{/if}
						</div>
						{#if multiple && !allowed && constraintMessage}
							<p
								id={`library-pick-reason-${item.id}`}
								class="mt-1 flex min-w-0 items-center gap-1 text-sm text-fg-subtle"
							>
								<Icon name="info" className="w-3 h-3 flex-shrink-0" />
								<span class="block truncate">{constraintMessage}</span>
							</p>
						{/if}
					</div>
				</svelte:fragment>
			</JustifiedLibraryGallery>
		{/if}
	</div>

	<svelte:fragment slot="footer">
		{#if multiple}
			<MediaPickerSelectionBar count={$selection.length} onCancel={handleCancel} onConfirm={handleConfirm} />
		{/if}
		{#if !isLoading && items.length > 0}
			<div class="p-3 md:p-4 flex items-center justify-between">
				<span class="text-xs md:text-sm text-fg-muted font-mono tabular-nums">
					{(currentPage - 1) * pageSize + 1}-{Math.min(currentPage * pageSize, total)} of {total}
				</span>

				{#if totalPages > 1}
					<Pagination {currentPage} {totalPages} size="sm" onPageChange={loadItems} />
				{/if}

				{#if !multiple}
					<button
						type="button"
						class="px-4 py-2 text-sm font-medium text-fg-muted hover:bg-surface-3/50 rounded transition-colors"
						on:click={onClose}
					>
						Close
					</button>
				{/if}
			</div>
		{/if}
	</svelte:fragment>
</MediaPickerFrame>
