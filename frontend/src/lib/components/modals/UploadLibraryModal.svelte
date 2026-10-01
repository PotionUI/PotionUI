<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onDestroy } from 'svelte';
	import { api } from '$lib/services/api/index';
	import type { LibraryItem } from '$lib/services/api/library';
	import Icon from '$lib/components/Icon.svelte';
	import MediaPickerFrame from './MediaPickerFrame.svelte';
	import MediaPickerSelectionBar from './MediaPickerSelectionBar.svelte';
	import MediaPickTile from './MediaPickTile.svelte';
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
	import { Badge, Button, Spinner, EmptyState } from '$lib/components/ui';
	import { buildLibraryQuery, DEFAULT_LIBRARY_FILTERS } from '$lib/library/libraryQuery';
	import { libraryItemDisplayName, libraryItemMetaParts } from '$lib/library/libraryItemMeta';
	import { LIBRARY_TAG_TYPE } from '$lib/stores/library';
	import type { Tag } from '$lib/types/history';

	// The media loader's "Library" picker. Reads the user's real Library
	// (/api/library/items) - the same items, tags and folders the Library page
	// shows - so anything curated there is pickable here.
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
	let isLoading = false;
	let loadError: string | null = null;
	let total = 0;
	let currentPage = 1;
	let pageSize = 20;

	// Per-item inline delete confirmation - a second modal on top of this one
	// would be heavier than the action warrants, so the delete button becomes
	// a "Confirm / Cancel" pair in place for the item being removed.
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
						mediaType: mediaType ?? 'all',
						selectedTagIds,
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
			const response = await api.getTags(LIBRARY_TAG_TYPE);
			if (response.success && response.data) availableTags = response.data.tags as Tag[];
		} catch (error) {
			logger.error('Failed to load library tags:', error);
		}
	}

	$: if (isOpen) {
		confirmingId = null;
		loadItems(1);
		loadTags();
	}

	let searchDebounce: ReturnType<typeof setTimeout> | undefined;
	function handleSearchInput(event: Event) {
		search = (event.currentTarget as HTMLInputElement).value;
		clearTimeout(searchDebounce);
		searchDebounce = setTimeout(() => loadItems(1), 300);
	}

	onDestroy(() => clearTimeout(searchDebounce));

	function handleTagToggle(tagId: string) {
		selectedTagIds = selectedTagIds.includes(tagId)
			? selectedTagIds.filter((id) => id !== tagId)
			: [...selectedTagIds, tagId];
		loadItems(1);
	}

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

	function metadataLine(item: LibraryItem): string | null {
		const parts = [
			item.width && item.height ? `${item.width}×${item.height}` : null,
			...libraryItemMetaParts(item)
		].filter(Boolean) as string[];
		return parts.length > 0 ? parts.join(' · ') : null;
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

	<!-- Modal Body -->
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
				title="Nothing here yet"
				description={`Files you upload, and generations you copy across, show up in your library so you can reuse them.${mediaType ? ` Nothing ${mediaTypeLabel}-shaped is in it yet.` : ''}`}
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
				<div class="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4">
					{#each items as item (item.id)}
						{@const allowed = isMediaTypeSelectable(item.media_type, selectableMediaTypes)}
						<MediaPickTile
							label={libraryItemDisplayName(item)}
							sublabel={metadataLine(item) ?? ''}
							checked={$selection.includes(item.id)}
							disabled={!allowed}
							disabledReason={constraintMessage}
							onToggle={() => picker.toggle(item.id, onSelectionChange)}
							onPreview={() => (previewId = item.id)}
						>
							{#if item.media_type === 'image'}
								<img
									src={item.thumbnail_medium || item.url}
									alt={libraryItemDisplayName(item)}
									class="max-w-full max-h-full object-contain"
								/>
							{:else if item.media_type === 'video'}
								<video src={item.url} class="max-w-full max-h-full object-contain" muted>
									<track kind="captions" />
								</video>
							{:else}
								<Icon name="audio" className="w-8 h-8 text-fg-muted" />
							{/if}
						</MediaPickTile>
					{/each}
				</div>
			{:else}
			<div class="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4">
				{#each items as item (item.id)}
					<div class="flex flex-col">
						<div class="relative border-2 border-line-strong rounded-lg overflow-hidden bg-surface-2 group">
							<button
								type="button"
								class="w-full aspect-video flex items-center justify-center cursor-pointer"
								on:click={() => onSelect(item)}
								aria-label={`Use ${libraryItemDisplayName(item)}`}
							>
								{#if item.media_type === 'image'}
									<img
										src={item.url}
										alt={libraryItemDisplayName(item)}
										class="max-w-full max-h-full object-contain"
									/>
								{:else if item.media_type === 'video'}
									<video src={item.url} class="max-w-full max-h-full object-contain" muted>
										<track kind="captions" />
									</video>
									<div class="absolute inset-0 flex items-center justify-center bg-black/20 pointer-events-none">
										<Icon name="video" className="w-8 h-8 text-white/90" />
									</div>
								{:else}
									<div class="flex flex-col items-center gap-2 text-fg-muted">
										<Icon name="audio" className="w-8 h-8" />
										<span class="text-xs">Audio</span>
									</div>
								{/if}
							</button>

							<!-- Delete affordance (top-right) -->
							<div class="absolute top-0 right-0 p-2 z-10">
								{#if confirmingId === item.id}
									<div class="flex items-center gap-1 bg-surface-1 rounded-md p-1 shadow-floating">
										<button
											type="button"
											class="px-2 py-1 text-xs font-medium text-danger hover:bg-danger/10 rounded"
											on:click={() => confirmDelete(item)}
											disabled={deletingId === item.id}
										>
											{deletingId === item.id ? '...' : 'Delete'}
										</button>
										<button
											type="button"
											class="px-2 py-1 text-xs font-medium text-fg-muted hover:bg-surface-3/50 rounded"
											on:click={cancelDelete}
											disabled={deletingId === item.id}
										>
											Cancel
										</button>
									</div>
								{:else}
									<button
										type="button"
										class="p-1.5 text-white/80 hover:text-white bg-black/50 hover:bg-black/70 rounded-md transition-colors opacity-0 group-hover:opacity-100"
										title="Delete this item"
										aria-label={`Delete ${libraryItemDisplayName(item)}`}
										on:click={() => requestDelete(item.id)}
									>
										<Icon name="trash" className="w-4 h-4" />
									</button>
								{/if}
							</div>

							<!-- File name overlay -->
							<div class="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/70 to-transparent p-2 pt-4 pointer-events-none">
								<p class="text-sm font-medium text-white truncate" title={libraryItemDisplayName(item)}>
									{libraryItemDisplayName(item)}
								</p>
							</div>
						</div>
						{#if metadataLine(item)}
							<p
								class="mt-1 font-mono tabular-nums text-xs text-fg-subtle truncate"
								title={metadataLine(item) ?? ''}
							>
								{metadataLine(item)}
							</p>
						{/if}
					</div>
				{/each}
			</div>
			{/if}
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
					<div class="flex items-center gap-2">
						<Button
							variant="secondary"
							size="sm"
							disabled={currentPage === 1}
							onclick={() => loadItems(currentPage - 1)}
						>
							Previous
						</Button>
						<span class="text-sm text-fg-muted font-mono tabular-nums">
							Page {currentPage} of {totalPages}
						</span>
						<Button
							variant="secondary"
							size="sm"
							disabled={currentPage === totalPages}
							onclick={() => loadItems(currentPage + 1)}
						>
							Next
						</Button>
					</div>
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
