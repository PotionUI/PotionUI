<script lang="ts">
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
	import AdminFilterBar from './AdminFilterBar.svelte';
	import { MasterDetailLayout, DetailEmptyState } from '$lib/components/master-detail';
	import { Pane, PaneGroupHeader } from '$lib/components/pane';
	import { Button, EmptyState, Alert, Spinner, Input, Kbd } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';

	type StatusFilter = 'all' | 'active' | 'pending' | 'completed' | 'failed';

	const SEARCH_INPUT_ID = 'downloads-search';

	let statusFilter: StatusFilter = 'all';
	let query = '';
	let showAddModal = false;
	let showSettingsModal = false;
	let selectedId: string | null = null;
	let providers: { id: string; name: string }[] = [];

	const statusFilters: { id: StatusFilter; label: string }[] = [
		{ id: 'all', label: 'All' },
		{ id: 'active', label: 'Active' },
		{ id: 'pending', label: 'Pending' },
		{ id: 'completed', label: 'Done' },
		{ id: 'failed', label: 'Failed' }
	];

	$: activeCount = $downloads.filter((d) => d.status === 'downloading' || d.status === 'paused').length;
	$: pendingCount = $downloads.filter((d) => d.status === 'pending').length;
	$: completedCount = $downloads.filter((d) => d.status === 'completed').length;
	$: failedCount = $downloads.filter((d) => d.status === 'failed' || d.status === 'cancelled').length;

	$: normalizedQuery = query.trim().toLowerCase();
	$: statusFilteredDownloads = (() => {
		switch (statusFilter) {
			case 'active':
				return $downloads.filter((d) => d.status === 'downloading' || d.status === 'paused');
			case 'pending':
				return $downloads.filter((d) => d.status === 'pending');
			case 'completed':
				return $downloads.filter((d) => d.status === 'completed');
			case 'failed':
				return $downloads.filter((d) => d.status === 'failed' || d.status === 'cancelled');
			default:
				return $downloads;
		}
	})();
	$: filteredDownloads = normalizedQuery
		? statusFilteredDownloads.filter(
				(d) =>
					d.filename.toLowerCase().includes(normalizedQuery) || d.url.toLowerCase().includes(normalizedQuery)
			)
		: statusFilteredDownloads;

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

	$: activeFilterCount = Number(!!query.trim()) + Number(statusFilter !== 'all');

	// Default selection to the first row once downloads have loaded; keeps
	// whatever's already selected stable across WebSocket-driven updates.
	$: if (!selectedId && $downloads.length > 0) {
		selectedId = $downloads[0].id;
	}

	onMount(async () => {
		try {
			await downloaderWebSocket.connectAsync();
			downloadStore.initializeWebSocket();
		} catch (err) {
			logger.error('Failed to connect downloader WebSocket:', err);
		}

		await downloadStore.loadDownloads();
		await downloadStore.loadSettings();
		await downloadStore.loadRemoteBackends();

		try {
			const res = await api.getProviders();
			if (res.success && res.data) {
				providers = (res.data as { id: string; name: string }[]).map((p) => ({ id: p.id, name: p.name }));
			}
		} catch (err) {
			logger.error('Failed to load providers:', err);
		}
	});

	onDestroy(() => {
		downloadStore.cleanupWebSocket();
		downloaderWebSocket.disconnect();
	});

	function handleGlobalKeydown(e: KeyboardEvent) {
		if (e.key !== '/') return;
		const target = e.target as HTMLElement | null;
		const tag = target?.tagName;
		if (tag === 'INPUT' || tag === 'TEXTAREA' || target?.isContentEditable) return;
		e.preventDefault();
		document.getElementById(SEARCH_INPUT_ID)?.focus();
	}

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
		query = '';
		statusFilter = 'all';
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

<svelte:window on:keydown={handleGlobalKeydown} />

<div class="flex h-[calc(100dvh-var(--header-h)-2rem)] min-h-[36rem] flex-col gap-4 sm:h-[calc(100dvh-var(--header-h)-3rem)]">
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
		{#snippet downloadsSearch()}
			<div class="relative flex-1 min-w-0">
				<Icon name="search" className="w-3.5 h-3.5 text-fg-subtle absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
				<Input
					id={SEARCH_INPUT_ID}
					bind:value={query}
					placeholder="Search downloads..."
					class="pl-8 pr-8 text-sm h-8"
				/>
				{#if !query}
					<span class="absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none">
						<Kbd keys="/" />
					</span>
				{/if}
			</div>
		{/snippet}
		{#snippet downloadStatusFilters()}
			<div class="flex items-center gap-1 bg-surface-2 border border-line rounded p-0.5">
				{#each statusFilters as f (f.id)}
					<button
						type="button"
						class="px-2.5 py-1 text-xs font-medium rounded transition-colors {statusFilter === f.id
							? 'bg-signal/10 text-signal'
							: 'text-fg-muted hover:text-fg hover:bg-surface-3'}"
						onclick={() => (statusFilter = f.id)}
					>
						{f.label}
					</button>
				{/each}
			</div>
		{/snippet}

		<AdminFilterBar
			search={downloadsSearch}
			filters={downloadStatusFilters}
			activeCount={activeFilterCount}
			onClear={clearFilters}
		/>

		<section class="flex-1 min-h-0 rounded-lg border border-line bg-surface-1 overflow-hidden">
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
