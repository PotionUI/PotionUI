<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { logger } from '$lib/utils/logger';
	import {
		downloadStore,
		downloads,
		loading,
		error,
		remoteBackends,
		statusLabel,
		type Download
	} from '$lib/stores/downloads';
	import { downloaderWebSocket, downloaderConnectionState } from '$lib/services/downloaderWebsocket';
	import { api } from '$lib/services/api/index';
	import { confirmDialog } from '$lib/stores/confirm';
	import DownloadDetail from './DownloadDetail.svelte';
	import AddDownloadModal from './AddDownloadModal.svelte';
	import DownloadSettingsModal from './DownloadSettingsModal.svelte';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import { DataTable, StatusCell, type DataTableColumn } from '$lib/components/table';
	import { selectPage, clearAll } from '$lib/components/table/selection';
	import SelectionActionBar from '$lib/components/collections/SelectionActionBar.svelte';
	import { DOWNLOAD_LIBRARY_SECTIONS, type DownloadLibrarySection } from './downloads/downloadLibrarySections';
	import { availableDownloadBulkActions } from './downloads/downloadBulkActions';
	import {
		DEFAULT_DOWNLOADER_FILTERS,
		DOWNLOADER_SORT_OPTIONS,
		applyDownloaderFilters,
		downloadSectionCounts,
		type DownloaderFilters,
		type DownloadSortBy
	} from './downloaderFilters';
	import { Button, EmptyState, Alert, Spinner, IconButton } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';

	let filters = $state<DownloaderFilters>({ ...DEFAULT_DOWNLOADER_FILTERS });
	let showAddModal = $state(false);
	let showSettingsModal = $state(false);
	let selected = $state<Set<string>>(new Set());
	let providers = $state<{ id: string; name: string }[]>([]);

	const section = $derived(($page.url.searchParams.get('section') as DownloadLibrarySection) || 'all');
	const viewId = $derived($page.url.searchParams.get('id'));
	const detailOpen = $derived(!!viewId);

	const sectionCounts = $derived(downloadSectionCounts($downloads));
	const filteredDownloads = $derived(applyDownloaderFilters($downloads, filters, section));
	const isFiltered = $derived(filteredDownloads.length !== $downloads.length);
	const bulkActions = $derived(availableDownloadBulkActions($downloads, selected));

	const selectedDownload = $derived(viewId ? ($downloads.find((d) => d.id === viewId) ?? null) : null);

	const destinationNameById = $derived(new Map($remoteBackends.map((b) => [b.id, b.name] as const)));
	const providerNameById = $derived(new Map(providers.map((p) => [p.id, p.name] as const)));

	const pendingInQueueOrder = $derived(
		$downloads
			.filter((d) => d.status === 'pending')
			.slice()
			.sort((a, b) => (a.created_at ?? '').localeCompare(b.created_at ?? ''))
	);

	const CONNECTION_CLASSES = {
		success: { text: 'text-success', dot: 'bg-success-solid' },
		warning: { text: 'text-warning', dot: 'bg-warning-solid' },
		danger: { text: 'text-danger', dot: 'bg-danger-solid' }
	} as const;

	const connection = $derived.by(() => {
		const state = $downloaderConnectionState;
		if (state === 'connected') return { label: 'Connected', ...CONNECTION_CLASSES.success, pulse: false };
		if (state === 'connecting' || state === 'reconnecting') {
			return { label: 'Connecting…', ...CONNECTION_CLASSES.warning, pulse: true };
		}
		return { label: 'Disconnected', ...CONNECTION_CLASSES.danger, pulse: false };
	});

	$effect(() => {
		void section;
		selected = new Set();
	});

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

	function buildUrl(overrides: { section?: DownloadLibrarySection; id?: string | null } = {}): string {
		const params = new URLSearchParams();
		params.set('tab', 'downloads');
		const nextSection = overrides.section ?? section;
		if (nextSection !== 'all') params.set('section', nextSection);
		const id = overrides.id !== undefined ? overrides.id : viewId;
		if (id) params.set('id', id);
		const query = params.toString();
		return query ? `${$page.url.pathname}?${query}` : $page.url.pathname;
	}

	function selectSection(id: DownloadLibrarySection) {
		void goto(buildUrl({ section: id, id: null }));
	}

	function openDownloadId(id: string) {
		void goto(buildUrl({ id }));
	}

	function backToList() {
		void goto(buildUrl({ id: null }));
	}

	function destinationNameFor(download: Download): string | null {
		return download.destination_backend_id
			? (destinationNameById.get(download.destination_backend_id) ?? download.destination_backend_id)
			: null;
	}

	function sourceHost(url: string): string {
		try {
			return new URL(url).hostname;
		} catch {
			return url;
		}
	}

	function statusTone(status: Download['status']): 'success' | 'danger' | 'warning' | 'info' | 'signal' | 'muted' {
		if (status === 'pending') return 'warning';
		if (status === 'downloading') return 'signal';
		if (status === 'completed') return 'success';
		if (status === 'failed') return 'danger';
		return 'muted';
	}

	function sizeLabel(download: Download): string {
		if (download.total_bytes) return downloadStore.formatBytes(download.total_bytes);
		if (download.downloaded_bytes) return downloadStore.formatBytes(download.downloaded_bytes);
		return '—';
	}

	function speedLabel(download: Download): string {
		return download.status === 'downloading' ? downloadStore.formatSpeed(download.speed_bytes_per_sec) : '—';
	}

	function completedLabel(download: Download): string {
		return download.completed_at ? downloadStore.formatTimestamp(download.completed_at) : '—';
	}

	function queuePositionOf(download: Download): number | undefined {
		const idx = pendingInQueueOrder.findIndex((d) => d.id === download.id);
		return idx >= 0 ? idx + 1 : undefined;
	}

	async function cancelOne(download: Download) {
		const ok = await confirmDialog({
			title: 'Cancel download',
			message: `Cancel "${download.filename}"?`,
			variant: 'warning'
		});
		if (ok) void downloadStore.cancelDownload(download.id);
	}

	async function removeOne(download: Download) {
		const ok = await confirmDialog({
			title: 'Remove from history',
			message: `Remove "${download.filename}" from history?`,
			variant: 'danger'
		});
		if (ok) void downloadStore.deleteDownload(download.id);
	}

	async function bulkRetry() {
		const ids = [...selected];
		selected = new Set();
		await Promise.all(ids.map((id) => downloadStore.retryDownload(id)));
	}

	async function bulkCancel() {
		const ok = await confirmDialog({
			title: 'Cancel downloads',
			message: `Cancel ${selected.size} download${selected.size === 1 ? '' : 's'}?`,
			variant: 'warning'
		});
		if (!ok) return;
		const ids = [...selected];
		selected = new Set();
		await Promise.all(ids.map((id) => downloadStore.cancelDownload(id)));
	}

	async function bulkRemove() {
		const ok = await confirmDialog({
			title: 'Remove from history',
			message: `Remove ${selected.size} download${selected.size === 1 ? '' : 's'} from history?`,
			variant: 'danger'
		});
		if (!ok) return;
		const ids = [...selected];
		selected = new Set();
		await Promise.all(ids.map((id) => downloadStore.deleteDownload(id)));
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

	const columns: DataTableColumn<Download>[] = $derived.by(() => [
		{ key: 'file', label: 'File', width: 'minmax(200px,2fr)', cell: fileCell },
		{ key: 'status', label: 'Status', width: '110px', cell: statusCell },
		{ key: 'progress', label: 'Progress', width: '150px', cell: progressCell },
		{ key: 'size', label: 'Size', width: '90px', mono: true, priority: 1, accessor: sizeLabel },
		{ key: 'speed', label: 'Speed', width: '90px', mono: true, priority: 1, accessor: speedLabel },
		{ key: 'source', label: 'Source', width: 'minmax(120px,1fr)', priority: 1, cell: sourceCell },
		{ key: 'destination', label: 'Destination', width: 'minmax(120px,1fr)', priority: 1, cell: destinationCell },
		{ key: 'completed', label: 'Completed', width: '110px', mono: true, priority: 1, accessor: completedLabel },
		{ key: 'retries', label: 'Retries', width: '70px', mono: true, priority: 2, accessor: (d) => d.retry_count }
	]);
</script>

{#snippet fileCell(download: Download)}
	<div class="flex items-center gap-2 min-w-0">
		<Icon name={download.type === 'media' ? 'image' : 'cube'} className="w-3.5 h-3.5 text-fg-subtle flex-shrink-0" />
		<Tooltip text={download.filename}>
			<span class="truncate">{download.filename}</span>
		</Tooltip>
	</div>
{/snippet}

{#snippet statusCell(download: Download)}
	<StatusCell tone={statusTone(download.status)} label={statusLabel(download.status)} />
{/snippet}

{#snippet progressCell(download: Download)}
	{#if download.status === 'downloading' || download.status === 'paused'}
		<div class="flex items-center gap-1.5 w-full">
			<div class="h-1 flex-1 rounded-sm bg-surface-3 overflow-hidden">
				<div
					class="h-full rounded-sm {download.status === 'downloading' ? 'bg-signal-solid' : 'bg-line-hover'}"
					style="width: {Math.round(download.progress * 100)}%"
				></div>
			</div>
			<span class="font-mono text-2xs tabular-nums text-fg-subtle flex-shrink-0">{Math.round(download.progress * 100)}%</span>
		</div>
	{:else if download.status === 'pending'}
		<span class="font-mono text-2xs tabular-nums text-fg-subtle">Queue {queuePositionOf(download) ?? '—'}</span>
	{:else}
		<span class="text-fg-subtle">—</span>
	{/if}
{/snippet}

{#snippet sourceCell(download: Download)}
	{#if download.provider_id && providerNameById.get(download.provider_id)}
		<span class="truncate">{providerNameById.get(download.provider_id)}</span>
	{:else}
		<span class="truncate font-mono text-xs text-fg-subtle">{sourceHost(download.url)}</span>
	{/if}
{/snippet}

{#snippet destinationCell(download: Download)}
	<span class="truncate">{destinationNameFor(download) ?? 'This machine'}</span>
{/snippet}

{#snippet rowCard(download: Download)}
	<div class="truncate text-sm font-semibold text-fg">{download.filename}</div>
	<div class="mt-1 flex items-center gap-2">
		<StatusCell tone={statusTone(download.status)} label={statusLabel(download.status)} />
		<span class="font-mono text-2xs text-fg-subtle">{sizeLabel(download)}</span>
	</div>
{/snippet}

{#snippet rowActions(download: Download)}
	<div class="flex items-center gap-1">
		{#if download.status === 'downloading'}
			<Tooltip text="Pause"><IconButton icon="pause" label="Pause" size="sm" onclick={() => downloadStore.pauseDownload(download.id)} /></Tooltip>
			<Tooltip text="Cancel"><IconButton icon="close" label="Cancel" size="sm" class="text-danger hover:bg-danger/10" onclick={() => cancelOne(download)} /></Tooltip>
		{:else if download.status === 'paused'}
			<Tooltip text="Resume"><IconButton icon="play" label="Resume" size="sm" onclick={() => downloadStore.resumeDownload(download.id)} /></Tooltip>
			<Tooltip text="Cancel"><IconButton icon="close" label="Cancel" size="sm" class="text-danger hover:bg-danger/10" onclick={() => cancelOne(download)} /></Tooltip>
		{:else if download.status === 'pending'}
			<Tooltip text="Cancel"><IconButton icon="close" label="Cancel" size="sm" class="text-danger hover:bg-danger/10" onclick={() => cancelOne(download)} /></Tooltip>
		{:else if download.status === 'failed'}
			<Tooltip text="Retry"><IconButton icon="refresh" label="Retry" size="sm" onclick={() => downloadStore.retryDownload(download.id)} /></Tooltip>
			<Tooltip text="Remove"><IconButton icon="trash" label="Remove" size="sm" class="text-danger hover:bg-danger/10" onclick={() => removeOne(download)} /></Tooltip>
		{:else}
			<Tooltip text="Download again"><IconButton icon="download" label="Download again" size="sm" onclick={() => downloadStore.retryDownload(download.id)} /></Tooltip>
			<Tooltip text="Remove"><IconButton icon="trash" label="Remove" size="sm" class="text-danger hover:bg-danger/10" onclick={() => removeOne(download)} /></Tooltip>
		{/if}
	</div>
{/snippet}

<LibraryShell
	title="Downloads"
	persistKey="admin-downloads-library"
	heightClass="h-full"
	sections={DOWNLOAD_LIBRARY_SECTIONS}
	{section}
	onSelectSection={selectSection}
	{sectionCounts}
	count={filteredDownloads.length}
	{detailOpen}
>
	{#snippet toolbar()}
		<LibraryFilterBar
			q={filters.q}
			onQueryChange={(value) => (filters = { ...filters, q: value })}
			searchPlaceholder="Search downloads…"
			sortBy={filters.sortBy}
			sortOptions={DOWNLOADER_SORT_OPTIONS}
			onSortChange={(value) => (filters = { ...filters, sortBy: value as DownloadSortBy })}
		/>
		<span class="inline-flex h-8 items-center gap-1.5 rounded border border-line px-2 font-mono text-xs {connection.text}">
			<span class="w-1.5 h-1.5 rounded-full {connection.dot} {connection.pulse ? 'animate-pulse' : ''}"></span>
			{connection.label}
		</span>
	{/snippet}

	{#snippet primary()}
		<Button variant="primary" size="sm" icon="plus" onclick={() => (showAddModal = true)}>Add download</Button>
	{/snippet}

	{#snippet overflow(close)}
		<button
			type="button"
			role="menuitem"
			class="w-full px-3 py-2 text-left text-xs flex items-center gap-2 hover:bg-surface-2"
			onclick={() => {
				close();
				showSettingsModal = true;
			}}
		>
			<Icon name="settings" className="w-3.5 h-3.5" />
			Settings
		</button>
		<button
			type="button"
			role="menuitem"
			disabled={(sectionCounts.completed ?? 0) === 0}
			class="w-full px-3 py-2 text-left text-xs flex items-center gap-2 hover:bg-surface-2 disabled:opacity-50 disabled:hover:bg-transparent"
			onclick={() => {
				close();
				void clearCompleted();
			}}
		>
			<Icon name="trash" className="w-3.5 h-3.5" />
			Clear completed
		</button>
	{/snippet}

	{#if detailOpen}
		{#if !selectedDownload}
			<div class="flex h-full items-center justify-center">
				{#if $loading}
					<Spinner size="lg" />
				{:else}
					<EmptyState title="Download not found" description="This download may have been removed from history." icon="download" compact>
						{#snippet actions()}<Button variant="ghost" size="sm" onclick={backToList}>Back to downloads</Button>{/snippet}
					</EmptyState>
				{/if}
			</div>
		{:else}
			<DownloadDetail
				download={selectedDownload}
				allDownloads={$downloads}
				destinationName={destinationNameFor(selectedDownload)}
				providerName={selectedDownload.provider_id ? (providerNameById.get(selectedDownload.provider_id) ?? null) : null}
				onBack={backToList}
				onSelectSibling={openDownloadId}
			/>
		{/if}
	{:else}
		<div class="flex flex-col gap-3 p-4">
			{#if $error}
				<Alert variant="danger" icon title="Error">
					{$error}
					{#snippet actions()}
						<button onclick={() => downloadStore.clearError()} class="text-danger hover:text-danger/80" aria-label="Dismiss error">
							<Icon name="close" className="w-4 h-4" />
						</button>
					{/snippet}
				</Alert>
			{/if}

			<SelectionActionBar
				active={selected.size > 0}
				selectedCount={selected.size}
				totalCount={filteredDownloads.length}
				onSelectAll={() => (selected = selectPage(selected, filteredDownloads.map((d) => d.id)))}
				onClearSelection={() => (selected = clearAll())}
				onClose={() => (selected = clearAll())}
			>
				<svelte:fragment slot="actionsBeforeCollection">
					{#if bulkActions.has('retry')}
						<button
							class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5"
							onclick={bulkRetry}
						>
							<Icon name="refresh" className="w-4 h-4" />
							Retry
						</button>
					{/if}
					{#if bulkActions.has('cancel')}
						<button
							class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5"
							onclick={bulkCancel}
						>
							<Icon name="close" className="w-4 h-4" />
							Cancel
						</button>
					{/if}
					{#if bulkActions.has('remove')}
						<button
							class="px-4 py-1.5 bg-danger-solid text-white text-sm rounded hover:bg-danger-solid/90 transition-colors flex items-center gap-2 font-medium"
							onclick={bulkRemove}
						>
							<Icon name="trash" className="w-4 h-4" />
							Remove
						</button>
					{/if}
				</svelte:fragment>
			</SelectionActionBar>

			<DataTable
				{columns}
				rows={filteredDownloads}
				getRowId={(d) => d.id}
				onRowClick={(d) => openDownloadId(d.id)}
				{selected}
				onSelectedChange={(next) => (selected = next)}
				loading={$loading && $downloads.length === 0}
				{isFiltered}
				{rowActions}
				card={rowCard}
			>
				{#snippet emptyState()}
					<EmptyState icon="download" title="No downloads yet" description="Queue a model or media file to download. Your download history will appear here.">
						{#snippet actions()}
							<Button variant="primary" icon="plus" onclick={() => (showAddModal = true)}>Add Your First Download</Button>
						{/snippet}
					</EmptyState>
				{/snippet}
				{#snippet filteredEmptyState()}
					<EmptyState icon="search" title="No downloads match" description="No downloads match the current section and search." compact>
						{#snippet actions()}
							<Button variant="ghost" size="sm" onclick={() => (filters = { ...DEFAULT_DOWNLOADER_FILTERS })}>Clear filters</Button>
						{/snippet}
					</EmptyState>
				{/snippet}
			</DataTable>
		</div>
	{/if}
</LibraryShell>

{#if showAddModal}
	<AddDownloadModal on:close={() => (showAddModal = false)} />
{/if}

{#if showSettingsModal}
	<DownloadSettingsModal on:close={() => (showSettingsModal = false)} />
{/if}
