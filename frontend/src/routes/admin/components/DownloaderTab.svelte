<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount, onDestroy } from 'svelte';
	import {
		downloadStore,
		downloads,
		downloadCounts,
		loading,
		error,
		activeDownloads,
		pendingDownloads,
		completedDownloads,
		failedDownloads,
		pausedDownloads,
		type Download,
		type DownloadStatus
	} from '$lib/stores/downloads';
	import { downloaderWebSocket, downloaderConnectionState } from '$lib/services/downloaderWebsocket';
	import { confirmDialog } from '$lib/stores/confirm';
	import DownloadItem from './DownloadItem.svelte';
	import AddDownloadModal from './AddDownloadModal.svelte';
	import DownloadSettingsModal from './DownloadSettingsModal.svelte';
	import { Button, EmptyState, Alert } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';

	type FilterTab = 'all' | 'active' | 'pending' | 'completed' | 'failed';
	let activeTab: FilterTab = 'all';
	let showAddModal = false;
	let showSettingsModal = false;

	// Filter downloads based on active tab
	$: filteredDownloads = (() => {
		switch (activeTab) {
			case 'active':
				return $downloads.filter((d) => d.status === 'downloading');
			case 'pending':
				return $downloads.filter((d) => d.status === 'pending' || d.status === 'paused');
			case 'completed':
				return $downloads.filter((d) => d.status === 'completed');
			case 'failed':
				return $downloads.filter((d) => d.status === 'failed' || d.status === 'cancelled');
			default:
				return $downloads;
		}
	})();

	// Tab counts
	$: activeCount = $downloads.filter((d) => d.status === 'downloading').length;
	$: pendingCount = $downloads.filter((d) => d.status === 'pending' || d.status === 'paused').length;
	$: completedCount = $downloads.filter((d) => d.status === 'completed').length;
	$: failedCount = $downloads.filter((d) => d.status === 'failed' || d.status === 'cancelled').length;

	onMount(async () => {
		// Connect to the downloader plugin's WebSocket
		try {
			await downloaderWebSocket.connectAsync();
			downloadStore.initializeWebSocket();
		} catch (err) {
			logger.error('Failed to connect downloader WebSocket:', err);
		}

		// Load initial data
		await downloadStore.loadDownloads();
		await downloadStore.loadSettings();
		await downloadStore.loadRemoteBackends();
	});

	onDestroy(() => {
		downloadStore.cleanupWebSocket();
		downloaderWebSocket.disconnect();
	});

	function getTabClass(tab: FilterTab): string {
		const baseClass = 'px-2.5 py-1 text-xs font-medium rounded-md transition-colors';
		if (tab === activeTab) {
			return `${baseClass} bg-signal/10 text-signal`;
		}
		return `${baseClass} text-fg-muted hover:bg-surface-2`;
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

<div class="space-y-4">
	<!-- Sub-header Bar with Tabs -->
	<div class="bg-surface-1/50 rounded-lg border border-line px-4 h-12 flex items-center gap-3">
		<div class="flex items-center gap-2">
			<svg class="w-4 h-4 text-fg-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M9 19l3 3m0 0l3-3m-3 3V10" />
			</svg>
			<span class="text-sm font-medium text-fg">Downloads</span>
		</div>
		<div class="h-5 w-px bg-line-strong"></div>
		<!-- Tab Navigation -->
		<div class="flex items-center gap-1">
			<button class={getTabClass('all')} on:click={() => (activeTab = 'all')}>
				All ({$downloads.length})
			</button>
			<button class={getTabClass('active')} on:click={() => (activeTab = 'active')}>
				Active ({activeCount})
			</button>
			<button class={getTabClass('pending')} on:click={() => (activeTab = 'pending')}>
				Pending ({pendingCount})
			</button>
			<button class={getTabClass('completed')} on:click={() => (activeTab = 'completed')}>
				Completed ({completedCount})
			</button>
			<button class={getTabClass('failed')} on:click={() => (activeTab = 'failed')}>
				Failed ({failedCount})
			</button>
		</div>
		<div class="h-5 w-px bg-line-strong"></div>
		<!-- Connection Status -->
		<div class="flex items-center gap-1.5 text-xs">
			{#if $downloaderConnectionState === 'connected'}
				<span class="flex items-center gap-1 text-success">
					<span class="w-1.5 h-1.5 rounded-full bg-success-solid"></span>
					Connected
				</span>
			{:else if $downloaderConnectionState === 'connecting' || $downloaderConnectionState === 'reconnecting'}
				<span class="flex items-center gap-1 text-warning">
					<span class="w-1.5 h-1.5 rounded-full bg-warning-solid animate-pulse"></span>
					Connecting...
				</span>
			{:else}
				<span class="flex items-center gap-1 text-danger">
					<span class="w-1.5 h-1.5 rounded-full bg-danger-solid"></span>
					Disconnected
				</span>
			{/if}
		</div>
		<div class="ml-auto flex items-center gap-2">
			{#if completedCount > 0 && (activeTab === 'all' || activeTab === 'completed')}
				<button
					on:click={clearCompleted}
					class="text-xs text-fg-subtle hover:text-fg-muted transition-colors"
				>
					Clear Completed
				</button>
			{/if}
			<button
				on:click={() => (showSettingsModal = true)}
				class="flex items-center gap-1.5 px-3 py-1.5 text-xs text-fg-muted hover:text-fg hover:bg-surface-2 rounded-lg border border-line transition-colors"
			>
				<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
				</svg>
				Settings
			</button>
			<button
				on:click={() => (showAddModal = true)}
				class="flex items-center gap-1.5 px-3 py-1.5 text-xs text-accent-contrast bg-accent hover:bg-accent-hover rounded-lg transition-colors"
			>
				<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
				</svg>
				Add Download
			</button>
		</div>
	</div>

	<!-- Error State -->
	{#if $error}
		<Alert variant="danger" icon title="Error">
			{$error}
			{#snippet actions()}
				<button
					on:click={() => downloadStore.clearError()}
					class="text-danger hover:text-danger/80"
					aria-label="Dismiss error"
				>
					<Icon name="close" className="w-5 h-5" />
				</button>
			{/snippet}
		</Alert>
	{/if}

	<!-- Loading State -->
	{#if $loading && $downloads.length === 0}
		<div class="flex items-center justify-center py-20">
			<div class="text-center">
				<div class="animate-spin rounded-full h-12 w-12 border-b-2 border-line-hover mx-auto mb-4"></div>
				<p class="text-fg-muted">Loading downloads...</p>
			</div>
		</div>

	<!-- Empty State -->
	{:else if filteredDownloads.length === 0}
		<EmptyState
			icon="download"
			title={activeTab === 'all'
				? 'No downloads yet'
				: activeTab === 'active'
					? 'No active downloads'
					: activeTab === 'pending'
						? 'No pending downloads'
						: activeTab === 'completed'
							? 'No completed downloads'
							: 'No failed downloads'}
			description={activeTab === 'all'
				? 'Queue a model or media file to download. Your download history will appear here.'
				: activeTab === 'active'
					? 'Nothing is downloading right now.'
					: activeTab === 'pending'
						? 'Nothing is waiting in the queue.'
						: activeTab === 'completed'
							? 'No downloads have finished yet.'
							: 'No downloads have failed or been cancelled.'}
		>
			{#snippet actions()}
				{#if activeTab === 'all'}
					<Button variant="primary" icon="plus" onclick={() => (showAddModal = true)}>
						Add Your First Download
					</Button>
				{/if}
			{/snippet}
		</EmptyState>

	<!-- Downloads List -->
	{:else}
		<div class="space-y-3">
			{#each filteredDownloads as download (download.id)}
				<DownloadItem {download} />
			{/each}
		</div>
	{/if}
</div>

<!-- Add Download Modal -->
{#if showAddModal}
	<AddDownloadModal on:close={() => (showAddModal = false)} />
{/if}

<!-- Settings Modal -->
{#if showSettingsModal}
	<DownloadSettingsModal on:close={() => (showSettingsModal = false)} />
{/if}
