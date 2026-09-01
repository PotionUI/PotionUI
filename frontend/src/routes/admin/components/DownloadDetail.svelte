<script lang="ts">
	import type { Snippet } from 'svelte';
	import {
		downloadStore,
		statusBadgeVariant,
		statusLabel,
		modelTypeFromPath,
		type Download
	} from '$lib/stores/downloads';
	import { confirmDialog } from '$lib/stores/confirm';
	import { Badge, Button, CopyButton } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';

	export let download: Download;
	export let allDownloads: Download[] = [];
	export let destinationName: string | null = null;
	export let providerName: string | null = null;
	export let onSelectSibling: (id: string) => void = () => {};

	let busy = false;

	$: modelType = modelTypeFromPath(download.destination_path);
	$: rowIcon = siblings.length > 1 ? 'layers' : download.type === 'media' ? 'image' : 'cube';
	$: progress = Math.round(download.progress * 100);
	$: siblings = download.group_id
		? allDownloads.filter((d) => d.group_id === download.group_id)
		: [];
	$: subtitleParts = [
		download.type.replace('_', ' ').toUpperCase(),
		modelType ? modelType.toUpperCase() : null
	].filter(Boolean) as string[];

	async function withBusy(fn: () => Promise<unknown>) {
		busy = true;
		try {
			await fn();
		} finally {
			busy = false;
		}
	}

	function handlePause() {
		void withBusy(() => downloadStore.pauseDownload(download.id));
	}

	function handleResume() {
		void withBusy(() => downloadStore.resumeDownload(download.id));
	}

	async function handleCancel() {
		const ok = await confirmDialog({
			title: 'Cancel download',
			message: `Cancel “${download.filename}”?`,
			variant: 'warning'
		});
		if (ok) void withBusy(() => downloadStore.cancelDownload(download.id));
	}

	function handleRetry() {
		void withBusy(() => downloadStore.retryDownload(download.id));
	}

	function handleDownloadAgain() {
		void withBusy(() => downloadStore.retryDownload(download.id));
	}

	async function handleRemove() {
		const ok = await confirmDialog({
			title: 'Remove from history',
			message: `Remove “${download.filename}” from history?`,
			variant: 'danger'
		});
		if (ok) void withBusy(() => downloadStore.deleteDownload(download.id));
	}
</script>

{#snippet section(title: string, body: Snippet)}
	<section class="rounded-lg border border-line bg-surface-1 shadow-raised">
		<div class="px-4 py-2.5 border-b border-line">
			<h3 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">{title}</h3>
		</div>
		<div class="px-4 py-4">
			{@render body()}
		</div>
	</section>
{/snippet}

<div class="flex items-start gap-3 px-4 sm:px-5 py-4 border-b border-line flex-shrink-0">
	<div class="flex-shrink-0 w-[34px] h-[34px] rounded bg-surface-2 border border-line-strong flex items-center justify-center text-fg-muted">
		<Icon name={rowIcon} className="w-[17px] h-[17px]" />
	</div>
	<div class="min-w-0 flex-1">
		<div class="flex items-center gap-2 min-w-0">
			<h2 class="text-sm font-semibold text-fg truncate" title={download.filename}>{download.filename}</h2>
			<Badge variant={statusBadgeVariant(download.status)} size="sm" class="flex-shrink-0 uppercase tracking-wide">
				{statusLabel(download.status)}
			</Badge>
		</div>
		<div class="flex items-center gap-1.5 mt-1 font-mono text-2xs text-fg-subtle flex-wrap">
			{#each subtitleParts as part, i}
				{#if i > 0}<span class="text-line-hover">·</span>{/if}
				<span>{part}</span>
			{/each}
			{#if destinationName}
				<span class="text-line-hover">·</span>
				<span class="inline-flex items-center gap-1 uppercase bg-surface-2 border border-line rounded px-1">
					<Icon name="server" className="w-2.5 h-2.5" />
					{destinationName}
				</span>
			{/if}
		</div>
	</div>
	<div class="flex items-center gap-2 flex-shrink-0">
		{#if download.status === 'downloading'}
			<Button variant="secondary" size="sm" icon="pause" disabled={busy} onclick={handlePause}>Pause</Button>
			<Button variant="ghost" size="sm" icon="close" class="text-danger" disabled={busy} onclick={handleCancel}>Cancel</Button>
		{:else if download.status === 'paused'}
			<Button variant="primary" size="sm" icon="play" disabled={busy} onclick={handleResume}>Resume</Button>
			<Button variant="ghost" size="sm" icon="close" class="text-danger" disabled={busy} onclick={handleCancel}>Cancel</Button>
		{:else if download.status === 'pending'}
			<Button variant="ghost" size="sm" icon="close" class="text-danger" disabled={busy} onclick={handleCancel}>Cancel</Button>
		{:else if download.status === 'failed'}
			<Button variant="primary" size="sm" icon="refresh" disabled={busy} onclick={handleRetry}>Retry</Button>
			<Button variant="secondary" size="sm" icon="download" disabled={busy} onclick={handleDownloadAgain}>Download again</Button>
			<Button variant="ghost" size="sm" icon="trash" class="text-danger" disabled={busy} onclick={handleRemove}>Remove</Button>
		{:else}
			<Button variant="primary" size="sm" icon="download" disabled={busy} onclick={handleDownloadAgain}>Download again</Button>
			<Button variant="ghost" size="sm" icon="trash" class="text-danger" disabled={busy} onclick={handleRemove}>Remove</Button>
		{/if}
	</div>
</div>

<div class="flex-1 min-h-0 overflow-y-auto px-4 sm:px-5 py-4 space-y-3">
	{#if download.status === 'pending'}
		{@render section('Queue', queueBody)}
	{:else if download.status === 'downloading' || download.status === 'paused'}
		{@render section('Transfer', transferBody)}
	{:else if download.status === 'completed'}
		{@render section('Result', resultBody)}
	{:else if download.status === 'failed'}
		{@render section('Error', errorBody)}
	{:else}
		{@render section('Transfer at cancel', cancelledBody)}
	{/if}

	{#if siblings.length > 1}
		{@render section(`Batch — ${siblings.length} files`, batchBody)}
	{/if}
</div>

{#snippet queueBody()}
	<div class="grid grid-cols-2 gap-x-6 gap-y-3">
		<div>
			<span class="block font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle mb-1">Added</span>
			<span class="text-sm text-fg font-mono tabular-nums">{downloadStore.formatTimestamp(download.created_at)}</span>
		</div>
		<div>
			<span class="block font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle mb-1">Destination</span>
			<span class="text-sm text-fg">{destinationName ?? 'This machine'}</span>
		</div>
		{#if providerName}
			<div>
				<span class="block font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle mb-1">Provider</span>
				<span class="inline-flex items-center gap-1.5 text-sm text-fg">
					<span class="w-[18px] h-[18px] rounded bg-surface-3 border border-line-strong flex items-center justify-center text-fg-muted flex-shrink-0">
						<Icon name="globe" className="w-2.5 h-2.5" />
					</span>
					{providerName}
				</span>
			</div>
		{/if}
	</div>
{/snippet}

{#snippet transferBody()}
	<div class="tick-ruler {download.status === 'paused' ? 'is-paused' : ''}">
		<div class="track"></div>
		<div class="fill" style="width: {progress}%"></div>
	</div>
	<div class="flex items-baseline gap-5 mt-3 flex-wrap">
		<div class="flex flex-col gap-0.5">
			<span class="font-mono text-sm font-semibold tabular-nums {download.status === 'downloading' ? 'text-signal' : 'text-fg'}">{progress}%</span>
			<span class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle">Progress</span>
		</div>
		<div class="flex flex-col gap-0.5">
			<span class="font-mono text-sm font-semibold tabular-nums text-fg">
				{download.total_bytes
					? `${downloadStore.formatBytes(download.downloaded_bytes)} / ${downloadStore.formatBytes(download.total_bytes)}`
					: downloadStore.formatBytes(download.downloaded_bytes)}
			</span>
			<span class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle">Received</span>
		</div>
		{#if download.status === 'downloading'}
			{#if download.speed_bytes_per_sec}
				<div class="flex flex-col gap-0.5">
					<span class="font-mono text-sm font-semibold tabular-nums text-fg">{downloadStore.formatSpeed(download.speed_bytes_per_sec)}</span>
					<span class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle">Speed</span>
				</div>
			{/if}
			{#if downloadStore.formatEta(download) !== '-'}
				<div class="flex flex-col gap-0.5">
					<span class="font-mono text-sm font-semibold tabular-nums text-fg">{downloadStore.formatEta(download)}</span>
					<span class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle">ETA</span>
				</div>
			{/if}
		{/if}
	</div>
	{#if download.status === 'downloading' && destinationName}
		<div class="flex items-center gap-2 mt-3 px-2.5 py-2 rounded bg-surface-2 border border-line">
			<Icon name="server" className="w-3.5 h-3.5 text-signal flex-shrink-0" />
			<span class="text-xs text-fg-muted">
				Fetching on pod <b class="text-fg font-semibold">{destinationName}</b> — not yet synced to this host
			</span>
		</div>
	{/if}
{/snippet}

{#snippet resultBody()}
	<div class="space-y-4">
		<div>
			<span class="block font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle mb-1.5">Destination path</span>
			<div class="flex items-center gap-2 bg-surface-2 border border-line rounded px-2.5 py-1.5">
				<span class="flex-1 min-w-0 overflow-x-auto whitespace-nowrap font-mono text-xs text-fg-muted">{download.destination_path}</span>
				<CopyButton text={download.destination_path} title="Copy path" />
			</div>
		</div>

		<div class="grid grid-cols-2 gap-x-6 gap-y-3">
			<div>
				<span class="block font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle mb-1">Destination backend</span>
				{#if destinationName}
					<span class="inline-flex items-center gap-1 text-xs font-mono uppercase text-fg-subtle bg-surface-2 border border-line rounded px-1.5 py-0.5">
						<Icon name="server" className="w-2.5 h-2.5" />
						{destinationName}
					</span>
				{:else}
					<span class="text-sm text-fg">This machine</span>
				{/if}
			</div>
			<div>
				<span class="block font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle mb-1">Size</span>
				<span class="text-sm font-mono tabular-nums text-fg">
					{download.total_bytes ? downloadStore.formatBytes(download.total_bytes) : 'Unknown'}
				</span>
			</div>
			{#if download.checksum_sha256}
				<div>
					<span class="block font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle mb-1">SHA-256</span>
					<span class="inline-flex items-center gap-1.5">
						<span class="font-mono text-xs text-fg-muted">
							{download.checksum_sha256.slice(0, 10)}…{download.checksum_sha256.slice(-8)}
						</span>
						<CopyButton text={download.checksum_sha256} title="Copy checksum" />
					</span>
				</div>
			{/if}
			{#if providerName}
				<div>
					<span class="block font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle mb-1">Provider</span>
					<span class="inline-flex items-center gap-1.5 text-sm text-fg">
						<span class="w-[18px] h-[18px] rounded bg-surface-3 border border-line-strong flex items-center justify-center text-fg-muted flex-shrink-0">
							<Icon name="globe" className="w-2.5 h-2.5" />
						</span>
						{providerName}
					</span>
				</div>
			{/if}
			{#if download.url}
				<div class="col-span-2">
					<span class="block font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle mb-1.5">Source URL</span>
					<div class="flex items-center gap-2 bg-surface-2 border border-line rounded px-2.5 py-1.5">
						<span class="flex-1 min-w-0 overflow-x-auto whitespace-nowrap font-mono text-xs text-fg-muted">{download.url}</span>
						<CopyButton text={download.url} title="Copy URL" />
					</div>
				</div>
			{/if}
		</div>

		{#if download.created_at || download.started_at || download.completed_at}
			<div>
				<span class="block font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle mb-2.5">Timeline</span>
				<div class="flex items-center">
					{#if download.created_at}
						<div class="flex flex-col gap-0.5">
							<span class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle">Queued</span>
							<span class="font-mono text-xs tabular-nums text-fg">{downloadStore.formatTimestamp(download.created_at)}</span>
						</div>
						<span class="flex-1 h-px mx-3 tl-line"></span>
					{/if}
					{#if download.started_at}
						<div class="flex flex-col gap-0.5">
							<span class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle">Started</span>
							<span class="font-mono text-xs tabular-nums text-fg">{downloadStore.formatTimestamp(download.started_at)}</span>
						</div>
						<span class="flex-1 h-px mx-3 tl-line"></span>
					{/if}
					{#if download.completed_at}
						<div class="flex flex-col gap-0.5">
							<span class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle">Completed</span>
							<span class="font-mono text-xs tabular-nums text-fg">{downloadStore.formatTimestamp(download.completed_at)}</span>
						</div>
					{/if}
				</div>
				{#if download.started_at && download.completed_at}
					{@const durationMs = new Date(download.completed_at).getTime() - new Date(download.started_at).getTime()}
					{#if durationMs > 0}
						<p class="text-xs text-fg-subtle mt-2">
							Transfer took <span class="font-mono text-fg-muted">{Math.round(durationMs / 1000)}s</span>
						</p>
					{/if}
				{/if}
			</div>
		{/if}

		{#if download.type === 'model'}
			<div class="flex items-center gap-2 px-3 py-2.5 rounded bg-success/10 border border-success/25">
				<Icon name="check-circle" className="w-3.5 h-3.5 text-success flex-shrink-0" />
				<span class="text-xs text-fg flex-1">
					Indexed into model catalog{#if modelType} as <b class="font-semibold">{modelType}</b>{/if}
				</span>
			</div>
		{/if}

		{#if download.tags && download.tags.length > 0}
			<div>
				<span class="block font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle mb-2">Tags</span>
				<div class="flex flex-wrap gap-1.5">
					{#each download.tags as tag}
						<span class="text-xs text-fg-muted bg-surface-2 border border-line rounded px-2 py-0.5">{tag}</span>
					{/each}
				</div>
			</div>
		{/if}
	</div>
{/snippet}

{#snippet errorBody()}
	<div class="font-mono text-xs leading-relaxed text-danger bg-danger/10 border border-danger/25 rounded px-3 py-2.5 whitespace-pre-wrap break-words">
		{download.error_message || 'Unknown error'}
	</div>
	<div class="flex items-center gap-5 mt-3 flex-wrap">
		<div class="flex flex-col gap-0.5">
			<span class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle">Retry count</span>
			<span class="text-sm text-fg">{download.retry_count}</span>
		</div>
	</div>
{/snippet}

{#snippet cancelledBody()}
	<div class="tick-ruler is-cancelled">
		<div class="track"></div>
		<div class="fill" style="width: {progress}%"></div>
	</div>
	<div class="flex items-baseline gap-5 mt-3 flex-wrap">
		<div class="flex flex-col gap-0.5">
			<span class="font-mono text-sm font-semibold tabular-nums text-fg">{progress}%</span>
			<span class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle">Reached</span>
		</div>
		<div class="flex flex-col gap-0.5">
			<span class="font-mono text-sm font-semibold tabular-nums text-fg">
				{download.total_bytes
					? `${downloadStore.formatBytes(download.downloaded_bytes)} / ${downloadStore.formatBytes(download.total_bytes)}`
					: downloadStore.formatBytes(download.downloaded_bytes)}
			</span>
			<span class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle">Received</span>
		</div>
	</div>
{/snippet}

{#snippet batchBody()}
	<div class="flex flex-col gap-1.5">
		{#each siblings as sibling (sibling.id)}
			<button
				type="button"
				class="flex items-center gap-2.5 px-2.5 py-1.5 rounded border text-left {sibling.id === download.id
					? 'border-signal/40 bg-signal/10'
					: 'border-line bg-surface-2 hover:bg-surface-3'}"
				onclick={() => onSelectSibling(sibling.id)}
			>
				<span class="w-[22px] h-[22px] rounded bg-surface-3 border border-line-strong flex items-center justify-center text-fg-subtle flex-shrink-0">
					<Icon name="layers" className="w-3 h-3" />
				</span>
				<span class="flex-1 min-w-0 truncate text-xs {sibling.id === download.id ? 'text-fg font-medium' : 'text-fg-muted'}">
					{sibling.filename}
				</span>
				<Badge variant={statusBadgeVariant(sibling.status)} size="sm">{statusLabel(sibling.status)}</Badge>
				{#if sibling.total_bytes}
					<span class="font-mono text-2xs tabular-nums text-fg-subtle flex-shrink-0">{downloadStore.formatBytes(sibling.total_bytes)}</span>
				{/if}
				{#if sibling.id === download.id}
					<span class="font-mono text-2xs uppercase tracking-wide text-signal flex-shrink-0">This file</span>
				{/if}
			</button>
		{/each}
	</div>
{/snippet}

<style>
	.tick-ruler {
		position: relative;
		height: 16px;
	}
	.tick-ruler .track {
		position: absolute;
		inset: 0;
		border-radius: 2px;
		background-color: rgb(var(--surface-3));
		background-image:
			repeating-linear-gradient(
				90deg,
				rgb(var(--line-hover)) 0,
				rgb(var(--line-hover)) 1px,
				transparent 1px,
				transparent 5%
			),
			repeating-linear-gradient(
				90deg,
				rgb(var(--line-hover)) 0,
				rgb(var(--line-hover)) 1px,
				transparent 1px,
				transparent 25%
			);
		background-size:
			100% 6px,
			100% 11px;
		background-position: bottom, bottom;
		background-repeat: repeat-x;
	}
	.tick-ruler .fill {
		position: absolute;
		left: 0;
		top: 0;
		bottom: 0;
		border-radius: 2px 0 0 2px;
		background: linear-gradient(180deg, rgb(var(--signal) / 0.85), rgb(var(--signal-solid) / 0.9));
	}
	.tick-ruler .fill::after {
		content: '';
		position: absolute;
		right: -1px;
		top: -3px;
		bottom: -3px;
		width: 2px;
		background: rgb(var(--signal));
		box-shadow: 0 0 8px rgb(var(--signal) / 0.9);
	}
	.tick-ruler.is-paused .fill {
		background: rgb(var(--line-hover));
	}
	.tick-ruler.is-paused .fill::after {
		display: none;
	}
	.tick-ruler.is-cancelled .fill {
		background: rgb(var(--fg-disabled) / 0.6);
	}
	.tick-ruler.is-cancelled .fill::after {
		display: none;
	}
	.tl-line {
		background-image: repeating-linear-gradient(
			90deg,
			rgb(var(--line-hover)) 0 4px,
			transparent 4px 8px
		);
	}
</style>
