<script lang="ts">
	import { downloadStore, remoteBackends, type Download } from '$lib/stores/downloads';
	import { confirmDialog } from '$lib/stores/confirm';
	import Icon from '$lib/components/Icon.svelte';
	import { Badge } from '$lib/components/ui';

	export let download: Download;

	$: destinationBackendName = download.destination_backend_id
		? $remoteBackends.find((b) => b.id === download.destination_backend_id)?.name ??
			download.destination_backend_id
		: null;

	$: progress = Math.round(download.progress * 100);
	$: sizeDisplay = download.total_bytes
		? `${downloadStore.formatBytes(download.downloaded_bytes)} / ${downloadStore.formatBytes(download.total_bytes)}`
		: downloadStore.formatBytes(download.downloaded_bytes);
	$: speedDisplay = downloadStore.formatSpeed(download.speed_bytes_per_sec);
	$: etaDisplay = downloadStore.formatEta(download);

	type BadgeVariant = 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'signal';

	function getStatusVariant(status: string): BadgeVariant {
		const variants: Record<string, BadgeVariant> = {
			pending: 'warning',
			downloading: 'signal',
			paused: 'neutral',
			completed: 'success',
			failed: 'danger',
			cancelled: 'neutral'
		};
		return variants[status] || 'neutral';
	}

	async function handlePause() {
		await downloadStore.pauseDownload(download.id);
	}

	async function handleResume() {
		await downloadStore.resumeDownload(download.id);
	}

	async function handleCancel() {
		const ok = await confirmDialog({
			title: 'Cancel download',
			message: 'Cancel this download?',
			variant: 'warning'
		});
		if (ok) {
			await downloadStore.cancelDownload(download.id);
		}
	}

	async function handleRetry() {
		await downloadStore.retryDownload(download.id);
	}

	async function handleDelete() {
		const ok = await confirmDialog({
			title: 'Remove from history',
			message: 'Remove this download from history?',
			variant: 'danger'
		});
		if (ok) {
			await downloadStore.deleteDownload(download.id);
		}
	}
</script>

<div class="bg-surface-1 border border-line rounded-lg p-4 shadow-raised">
	<div class="flex items-start gap-4">
		<!-- Type Icon -->
		<div class="flex-shrink-0 w-10 h-10 rounded-lg bg-surface-2 border border-line flex items-center justify-center">
			<Icon name={download.type === 'model' ? 'model' : 'image'} className="w-5 h-5 text-fg-subtle" />
		</div>

		<!-- Content -->
		<div class="flex-1 min-w-0">
			<!-- Header Row -->
			<div class="flex items-center justify-between mb-2">
				<div class="flex items-center gap-2 min-w-0">
					<h4 class="font-medium text-fg truncate" title={download.filename}>
						{download.filename}
					</h4>
					<Badge variant={getStatusVariant(download.status)} size="sm" class="flex-shrink-0 uppercase tracking-wide">
						{download.status}
					</Badge>
					{#if destinationBackendName}
						<Badge variant="neutral" size="sm" class="flex-shrink-0 font-mono">
							{destinationBackendName}
						</Badge>
					{/if}
				</div>

				<!-- Actions -->
				<div class="flex items-center gap-1 flex-shrink-0 ml-4">
					{#if download.status === 'downloading'}
						<button
							on:click={handlePause}
							class="p-2 text-fg-subtle hover:text-fg hover:bg-surface-3/50 rounded transition-colors"
							title="Pause"
							aria-label="Pause download"
						>
							<Icon name="pause" className="w-4 h-4" />
						</button>
						<button
							on:click={handleCancel}
							class="p-2 text-fg-subtle hover:text-danger hover:bg-danger/10 rounded transition-colors"
							title="Cancel"
							aria-label="Cancel download"
						>
							<Icon name="close" className="w-4 h-4" />
						</button>
					{:else if download.status === 'paused'}
						<button
							on:click={handleResume}
							class="p-2 text-fg-subtle hover:text-success hover:bg-success/10 rounded transition-colors"
							title="Resume"
							aria-label="Resume download"
						>
							<Icon name="play" className="w-4 h-4" />
						</button>
						<button
							on:click={handleCancel}
							class="p-2 text-fg-subtle hover:text-danger hover:bg-danger/10 rounded transition-colors"
							title="Cancel"
							aria-label="Cancel download"
						>
							<Icon name="close" className="w-4 h-4" />
						</button>
					{:else if download.status === 'pending'}
						<button
							on:click={handleCancel}
							class="p-2 text-fg-subtle hover:text-danger hover:bg-danger/10 rounded transition-colors"
							title="Cancel"
							aria-label="Cancel download"
						>
							<Icon name="close" className="w-4 h-4" />
						</button>
					{:else if download.status === 'failed'}
						<button
							on:click={handleRetry}
							class="p-2 text-fg-subtle hover:text-fg hover:bg-surface-3/50 rounded transition-colors"
							title="Retry"
							aria-label="Retry download"
						>
							<Icon name="refresh" className="w-4 h-4" />
						</button>
						<button
							on:click={handleDelete}
							class="p-2 text-fg-subtle hover:text-danger hover:bg-danger/10 rounded transition-colors"
							title="Delete"
							aria-label="Delete download"
						>
							<Icon name="trash" className="w-4 h-4" />
						</button>
					{:else}
						<button
							on:click={handleDelete}
							class="p-2 text-fg-subtle hover:text-danger hover:bg-danger/10 rounded transition-colors"
							title="Delete"
							aria-label="Delete download"
						>
							<Icon name="trash" className="w-4 h-4" />
						</button>
					{/if}
				</div>
			</div>

			<!-- Progress Bar (only for active downloads) -->
			{#if download.status === 'downloading' || download.status === 'paused'}
				<div class="mb-2">
					<div class="h-1.5 bg-surface-3 rounded-sm overflow-hidden">
						<div
							class="h-full {download.status === 'downloading' ? 'bg-signal-solid' : 'bg-line-strong'} transition-all duration-300"
							style="width: {progress}%"
						></div>
					</div>
				</div>
			{/if}

			<!-- Stats Row -->
			<div class="flex items-center gap-4 text-xs font-mono tabular-nums text-fg-subtle">
				{#if download.status === 'downloading'}
					<span>{progress}%</span>
					<span>{sizeDisplay}</span>
					<span>{speedDisplay}</span>
					{#if etaDisplay !== '-'}
						<span>ETA: {etaDisplay}</span>
					{/if}
				{:else if download.status === 'paused'}
					<span>{progress}%</span>
					<span>{sizeDisplay}</span>
					<span class="text-warning">Paused</span>
				{:else if download.status === 'completed'}
					<span>{download.total_bytes ? downloadStore.formatBytes(download.total_bytes) : 'Unknown size'}</span>
					{#if download.completed_at}
						<span>Completed {new Date(download.completed_at).toLocaleString()}</span>
					{/if}
				{:else if download.status === 'failed'}
					<span class="text-danger font-sans">{download.error_message || 'Unknown error'}</span>
					{#if download.retry_count > 0}
						<span>({download.retry_count} retries)</span>
					{/if}
				{:else if download.status === 'pending'}
					<span>Waiting in queue...</span>
				{:else}
					<span>Cancelled</span>
				{/if}
			</div>

			<!-- Tags (for model downloads) -->
			{#if download.tags && download.tags.length > 0}
				<div class="flex items-center gap-2 mt-2 flex-wrap">
					{#each download.tags as tag}
						<span class="text-xs px-2 py-0.5 rounded bg-surface-2 text-fg-muted">{tag}</span>
					{/each}
				</div>
			{/if}

			<!-- URL (collapsed) -->
			<div class="mt-2 text-xs font-mono text-fg-subtle truncate" title={download.url}>
				{download.url}
			</div>
		</div>
	</div>
</div>
