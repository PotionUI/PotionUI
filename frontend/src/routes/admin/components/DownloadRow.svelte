<script lang="ts">
	import { downloadStore, statusBadgeVariant, statusLabel, type Download } from '$lib/stores/downloads';
	import { Badge } from '$lib/components/ui';
	import { PaneRow } from '$lib/components/pane';
	import Icon from '$lib/components/Icon.svelte';

	export let download: Download;
	export let selected: boolean = false;
	export let onclick: () => void;
	/** Count of downloads sharing `group_id` (including this one) - <=1 means not a batch. */
	export let siblingCount: number = 0;
	/** 1-based position within the pending queue; only meaningful when pending. */
	export let queuePosition: number | undefined = undefined;
	export let destinationName: string | null = null;

	$: isBatch = siblingCount > 1;
	$: rowIcon = isBatch ? 'layers' : download.type === 'media' ? 'image' : 'cube';
	$: progress = Math.round(download.progress * 100);
</script>

{#snippet leading()}
	<div
		class="flex-shrink-0 w-[30px] h-[30px] rounded border flex items-center justify-center {selected
			? 'text-signal border-signal/40'
			: 'text-fg-subtle border-line-strong bg-surface-2'}"
	>
		<Icon name={rowIcon} className="w-4 h-4" />
	</div>
{/snippet}

{#snippet body()}
	<div class="flex items-center gap-1.5 min-w-0">
		<span class="text-[12.5px] font-medium truncate {selected ? 'text-fg' : 'text-fg-muted'}" title={download.filename}>
			{download.filename}
		</span>
		<Badge variant={statusBadgeVariant(download.status)} size="sm" class="flex-shrink-0 uppercase tracking-wide">
			{statusLabel(download.status)}
		</Badge>
	</div>

	<div class="flex items-center gap-1.5 mt-1 flex-wrap font-mono text-2xs tabular-nums text-fg-subtle">
		{#if download.status === 'pending'}
			<span>QUEUE POS {queuePosition ?? '-'}</span>
		{:else if download.status === 'downloading'}
			<span>{progress}%</span>
			<span class="text-fg-disabled">·</span>
			<span>
				{download.total_bytes
					? `${downloadStore.formatBytes(download.downloaded_bytes)}/${downloadStore.formatBytes(download.total_bytes)}`
					: downloadStore.formatBytes(download.downloaded_bytes)}
			</span>
			{#if download.speed_bytes_per_sec}
				<span class="text-fg-disabled">·</span>
				<span>{downloadStore.formatSpeed(download.speed_bytes_per_sec)}</span>
			{/if}
		{:else if download.status === 'paused'}
			<span>{progress}%</span>
			<span class="text-fg-disabled">·</span>
			<span>
				{download.total_bytes
					? `${downloadStore.formatBytes(download.downloaded_bytes)}/${downloadStore.formatBytes(download.total_bytes)}`
					: downloadStore.formatBytes(download.downloaded_bytes)}
			</span>
		{:else if download.status === 'completed'}
			{#if download.total_bytes}
				<span>{downloadStore.formatBytes(download.total_bytes)}</span>
				<span class="text-fg-disabled">·</span>
			{/if}
			<span>{downloadStore.formatTimestamp(download.completed_at)}</span>
		{:else if download.status === 'failed'}
			<span>{download.retry_count} RETR{download.retry_count === 1 ? 'Y' : 'IES'}</span>
			{#if download.error_message}
				<span class="text-fg-disabled">·</span>
				<span class="truncate text-danger normal-case">{download.error_message}</span>
			{/if}
		{:else if download.downloaded_bytes > 0}
			<span>{downloadStore.formatBytes(download.downloaded_bytes)} AT CANCEL</span>
		{:else}
			<span>Cancelled</span>
		{/if}

		{#if isBatch}
			<span
				class="inline-flex items-center font-mono text-2xs uppercase tracking-wide text-fg-subtle bg-surface-2 border border-line rounded px-1"
			>
				{siblingCount} IN BATCH
			</span>
		{:else if destinationName}
			<span
				class="inline-flex items-center gap-1 font-mono text-2xs uppercase tracking-wide text-fg-subtle bg-surface-2 border border-line rounded px-1"
			>
				<Icon name="server" className="w-2.5 h-2.5" />
				{destinationName}
			</span>
		{/if}
	</div>

	{#if download.status === 'downloading' || download.status === 'paused'}
		<div class="h-[3px] rounded-sm bg-surface-3 overflow-hidden mt-1.5">
			<div
				class="h-full rounded-sm {download.status === 'downloading' ? 'bg-signal-solid' : 'bg-line-hover'}"
				style="width: {progress}%"
			></div>
		</div>
	{/if}
{/snippet}

<PaneRow {selected} {onclick} leading={leading} children={body} />
