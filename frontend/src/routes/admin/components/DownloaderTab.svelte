<script lang="ts">
	import Card from '$lib/components/ui/Card.svelte';
	import { logger } from '$lib/utils/logger';
	import { onMount, onDestroy } from 'svelte';
	import { downloadStore, downloads, loading, error, remoteBackends, type Download } from '$lib/stores/downloads';
	import { downloaderWebSocket, downloaderConnectionState } from '$lib/services/downloaderWebsocket';
	import { api } from '$lib/services/api/index';
	import { confirmDialog } from '$lib/stores/confirm';
	import DownloadRow from './DownloadRow.svelte';
	import DownloadDetail from './DownloadDetail.svelte';
	import AddDownloadModal from './AddDownloadModal.svelte';
	import DownloadSettingsModal from './DownloadSettingsModal.svelte';
	import AdminTabShell from './AdminTabShell.svelte';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import LibraryFilterChipRow from '$lib/components/library/LibraryFilterChipRow.svelte';
	import FilterPopoverFrame from '$lib/components/library/FilterPopoverFrame.svelte';
	import SegmentedFilterGroup from '$lib/components/library/SegmentedFilterGroup.svelte';
	import {
		DEFAULT_DOWNLOADER_FILTERS,
		DOWNLOADER_SORT_OPTIONS,
		DOWNLOAD_STATUS_OPTIONS,
		applyDownloaderFilters,
		clearAllDownloaderFilters,
		clearDownloaderFilterChip,
		downloaderFilterActiveCount,
		downloaderFilterChips,
		type DownloaderFilters,
		type DownloadSortBy,
		type DownloadStatusFilter
	} from './downloaderFilters';
	import { MasterDetailLayout, DetailEmptyState } from '$lib/components/master-detail';
	import { Pane, PaneGroupHeader } from '$lib/components/pane';
	import { Button, EmptyState, Alert, Spinner } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';

	let filters: DownloaderFilters = { ...DEFAULT_DOWNLOADER_FILTERS };
	let showAddModal = false;
	let showSettingsModal = false;
	let selectedId: string | null = null;
	let providers: { id: string; name: string }[] = [];

	$: activeCount = $downloads.filter((d) => d.status === 'downloading' || d.status === 'paused').length;
	$: pendingCount = $downloads.filter((d) => d.status === 'pending').length;
	$: completedCount = $downloads.filter((d) => d.status === 'completed').length;
	$: failedCount = $downloads.filter((d) => d.status === 'failed' || d.status === 'cancelled').length;

	$: filteredDownloads = applyDownloaderFilters($downloads, filters);
	$: filterChips = downloaderFilterChips(filters);
	$: activeFilterCount = downloaderFilterActiveCount(filters);

	// Bucketed in the mock's display order - active/paused surface without a
	// header (it's "what's happening now"), the rest get a labelled group.
	$: activeBucket = filteredDownloads.filter((d) => d.status === 'downloading' || d.status === 'paused');
	$: pendingBucket = filteredDownloads.filter((d) => d.status === 'pending');
	$: completedBucket = filteredDownloads.filter((d) => d.status === 'completed');
	$: failedBucket = filteredDownloads.filter((d) => d.status === 'failed' || d.status === 'cancelled');

	// FIFO queue order (oldest first) for the "QUEUE POS n" label - the store's
	// own array is newest-first (matches GET /api/downloads's ORDER BY).
	$: pendingInQueueOrder = $downloads
		.filter((d) => d.status === 'pending')
		.slice()
		.sort((a, b) => (a.created_at ?? '').localeCompare(b.created_at ?? ''));

	$: groupSiblingCounts = (() => {
		const counts = new Map<string, number>();
		for (const d of $downloads) {
			if (!d.group_id) continue;
			counts.set(d.group_id, (counts.get(d.group_id) ?? 0) + 1);
		}
		return counts;
	})();

	$: destinationNameById = new Map($remoteBackends.map((b) => [b.id, b.name] as const));
	$: providerNameById = new Map(providers.map((p) => [p.id, p.name] as const));

	$: selectedDownload = selectedId ? ($downloads.find((d) => d.id === selectedId) ?? null) : null;

	// Default selection to the first row once downloads have loaded; keeps
	// whatever's already selected stable across WebSocket-driven updates.
	$: if (!selectedId && $downloads.length > 0) {
		selectedId = $downloads[0].id;
	}

	// Owns this mount's async onMount chain: connectAsync() and every serial
	// load below can still be pending when the component is torn down, and
	// the store's own session guard can't see that - a stale continuation
	// that calls initializeWebSocket()/loadDownloads() after destroy becomes
	// the store's new "current" session since nothing else has claimed it,
	// leaking WS handlers and publishing into a component nobody sees. Checked
	// after every await, including the rejected-connect catch path.
	let destroyed = false;

	onMount(async () => {
		try {
			await downloaderWebSocket.connectAsync();
			if (destroyed) return;
			downloadStore.initializeWebSocket();
		} catch (err) {
			if (!destroyed) logger.error('Failed to connect downloader WebSocket:', err);
		}

		if (destroyed) return;
		await downloadStore.loadDownloads();
		if (destroyed) return;
		await downloadStore.loadSettings();
		if (destroyed) return;
		await downloadStore.loadRemoteBackends();
		if (destroyed) return;

		try {
			const res = await api.getProviders();
			if (destroyed) return;
			if (res.success && res.data) {
				providers = (res.data as { id: string; name: string }[]).map((p) => ({ id: p.id, name: p.name }));
			}
		} catch (err) {
			if (!destroyed) logger.error('Failed to load providers:', err);
		}
	});

	onDestroy(() => {
		destroyed = true;
		downloadStore.cleanupWebSocket();
		downloaderWebSocket.disconnect();
	});

	function selectDownload(id: string) {
		selectedId = id;
	}

	function destinationNameFor(download: Download): string | null {
		return download.destination_backend_id
			? (destinationNameById.get(download.destination_backend_id) ?? download.destination_backend_id)
			: null;
	}

	function queuePositionOf(download: Download): number | undefined {
		const idx = pendingInQueueOrder.findIndex((d) => d.id === download.id);
		return idx >= 0 ? idx + 1 : undefined;
	}

	function clearFilters() {
		filters = { ...DEFAULT_DOWNLOADER_FILTERS };
	}

	async function clearCompleted() {
		if (
			await confirmDialog({
				title: 'Clear completed downloads',
				message: 'Clear all completed downloads from history?',
				variant: 'warning'
			})
		) {
			await downloadStore.clearCompleted();
		}
	}
</script>

<div class="flex min-h-[calc(100dvh-var(--header-h)-2rem)] flex-col gap-4 sm:min-h-[calc(100dvh-var(--header-h)-3rem)]">
	<AdminTabShell
		title="Downloads"
		icon="download"
		counts={[
			{ label: 'total', value: $downloads.length },
			{ label: 'active', value: activeCount, tone: 'info' },
			{ label: 'pending', value: pendingCount },
			{ label: 'done', value: completedCount, tone: 'success' },
			{ label: 'failed', value: failedCount }
		]}
	>
		{#snippet actions()}
			<div class="flex items-center gap-1.5 text-xs flex-shrink-0">
				{#if $downloaderConnectionState === 'connected'}
					<span class="flex items-center gap-1.5 text-success">
						<span class="w-1.5 h-1.5 rounded-full bg-success-solid"></span>Connected
					</span>
				{:else if $downloaderConnectionState === 'connecting' || $downloaderConnectionState === 'reconnecting'}
					<span class="flex items-center gap-1.5 text-warning">
						<span class="w-1.5 h-1.5 rounded-full bg-warning-solid animate-pulse"></span>Connecting…
					</span>
				{:else}
					<span class="flex items-center gap-1.5 text-danger">
						<span class="w-1.5 h-1.5 rounded-full bg-danger-solid"></span>Disconnected
					</span>
				{/if}
			</div>
			{#if completedCount > 0}
				<Button variant="ghost" size="sm" onclick={clearCompleted}>Clear completed</Button>
			{/if}
			<Button variant="secondary" size="sm" icon="settings" onclick={() => (showSettingsModal = true)}>
				Settings
			</Button>
			<Button variant="primary" size="sm" icon="plus" onclick={() => (showAddModal = true)}>Add download</Button>
		{/snippet}
	</AdminTabShell>

	{#if $error}
		<Alert variant="danger" icon title="Error">
			{$error}
			{#snippet actions()}
				<button
					onclick={() => downloadStore.clearError()}
					class="text-danger hover:text-danger/80"
					aria-label="Dismiss error"
				>
					<Icon name="close" className="w-4 h-4" />
				</button>
			{/snippet}
		</Alert>
	{/if}

	{#if $loading && $downloads.length === 0}
		<div class="flex-1 flex items-center justify-center">
			<div class="text-center">
				<Spinner size="lg" />
				<p class="text-fg-muted mt-4">Loading downloads...</p>
			</div>
		</div>
	{:else if $downloads.length === 0}
		<EmptyState
			icon="download"
			title="No downloads yet"
			description="Queue a model or media file to download. Your download history will appear here."
		>
			{#snippet actions()}
				<Button variant="primary" icon="plus" onclick={() => (showAddModal = true)}>
					Add Your First Download
				</Button>
			{/snippet}
		</EmptyState>
	{:else}
		<Card padding="none" class="flex flex-wrap items-center gap-2 px-4 py-2.5">
			<LibraryFilterBar
				q={filters.q}
				onQueryChange={(value) => (filters = { ...filters, q: value })}
				searchPlaceholder="Search downloads…"
				sortBy={filters.sortBy}
				sortOptions={DOWNLOADER_SORT_OPTIONS}
				onSortChange={(value) => (filters = { ...filters, sortBy: value as DownloadSortBy })}
				filterCount={activeFilterCount}
			>
				{#snippet popover(close: () => void)}
					<FilterPopoverFrame
						label="Download filters"
						onClearAll={() => (filters = clearAllDownloaderFilters(filters))}
						onClose={close}
					>
						<SegmentedFilterGroup
							label="Status"
							options={DOWNLOAD_STATUS_OPTIONS}
							value={filters.status}
							onChange={(status: DownloadStatusFilter) => (filters = { ...filters, status })}
						/>
					</FilterPopoverFrame>
				{/snippet}
			</LibraryFilterBar>
		</Card>

		<LibraryFilterChipRow
			chips={filterChips}
			onRemoveChip={(key) => (filters = clearDownloaderFilterChip(filters, key))}
			onClearAll={() => (filters = clearAllDownloaderFilters(filters))}
			loadedCount={filteredDownloads.length}
			total={$downloads.length}
		/>

		<section class="flex flex-1 flex-col rounded-lg border border-line bg-surface-1 overflow-hidden">
			<MasterDetailLayout leftWidth={360} minWidth={300} maxWidth={480} storageKey="admin-downloads-width">
				<div slot="list" class="h-full min-h-0">
					<Pane
						label="Downloads"
						count={filteredDownloads.length}
						isEmpty={filteredDownloads.length === 0}
						bodyRole="listbox"
						ariaLabel="Downloads"
					>
						{#snippet empty()}
							<div class="p-4 h-full flex items-center justify-center">
								<EmptyState
									title="No downloads match"
									description="No downloads match the current search and filters."
									icon="search"
									compact
								>
									{#snippet actions()}
										<Button variant="ghost" size="sm" onclick={clearFilters}>Clear filters</Button>
									{/snippet}
								</EmptyState>
							</div>
						{/snippet}

						{#snippet children()}
							{#each activeBucket as download (download.id)}
								<DownloadRow
									{download}
									selected={selectedId === download.id}
									onclick={() => selectDownload(download.id)}
									siblingCount={download.group_id ? (groupSiblingCounts.get(download.group_id) ?? 0) : 0}
									destinationName={destinationNameFor(download)}
								/>
							{/each}

							{#if pendingBucket.length > 0}
								<PaneGroupHeader label="Pending" count={pendingBucket.length} />
								{#each pendingBucket as download (download.id)}
									<DownloadRow
										{download}
										selected={selectedId === download.id}
										onclick={() => selectDownload(download.id)}
										siblingCount={download.group_id ? (groupSiblingCounts.get(download.group_id) ?? 0) : 0}
										destinationName={destinationNameFor(download)}
										queuePosition={queuePositionOf(download)}
									/>
								{/each}
							{/if}

							{#if completedBucket.length > 0}
								<PaneGroupHeader label="Completed" count={completedBucket.length} />
								{#each completedBucket as download (download.id)}
									<DownloadRow
										{download}
										selected={selectedId === download.id}
										onclick={() => selectDownload(download.id)}
										siblingCount={download.group_id ? (groupSiblingCounts.get(download.group_id) ?? 0) : 0}
										destinationName={destinationNameFor(download)}
									/>
								{/each}
							{/if}

							{#if failedBucket.length > 0}
								<PaneGroupHeader label="Failed" count={failedBucket.length} />
								{#each failedBucket as download (download.id)}
									<DownloadRow
										{download}
										selected={selectedId === download.id}
										onclick={() => selectDownload(download.id)}
										siblingCount={download.group_id ? (groupSiblingCounts.get(download.group_id) ?? 0) : 0}
										destinationName={destinationNameFor(download)}
									/>
								{/each}
							{/if}
						{/snippet}
					</Pane>
				</div>

				<div slot="detail" class="h-full min-h-0 flex flex-col">
					{#if selectedDownload}
						{#key selectedDownload.id}
							<DownloadDetail
								download={selectedDownload}
								allDownloads={$downloads}
								destinationName={destinationNameFor(selectedDownload)}
								providerName={selectedDownload.provider_id
									? (providerNameById.get(selectedDownload.provider_id) ?? null)
									: null}
								onSelectSibling={selectDownload}
							/>
						{/key}
					{:else}
						<DetailEmptyState message="Select a download to view details" icon="document" />
					{/if}
				</div>
			</MasterDetailLayout>
		</section>
	{/if}
</div>

{#if showAddModal}
	<AddDownloadModal on:close={() => (showAddModal = false)} />
{/if}

{#if showSettingsModal}
	<DownloadSettingsModal on:close={() => (showSettingsModal = false)} />
{/if}
