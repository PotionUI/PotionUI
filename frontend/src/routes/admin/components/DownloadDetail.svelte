<script lang="ts">
	import { parseServerDate } from '$lib/utils/relativeTime';
	import {
		downloadStore,
		statusBadgeVariant,
		statusLabel,
		modelTypeFromPath,
		type Download
	} from '$lib/stores/downloads';
	import { confirmDialog } from '$lib/stores/confirm';
	import { Badge, Button, CopyButton, IconButton } from '$lib/components/ui';
	import { DetailHeader, DetailBody, DetailLayout, DetailSection, KVGrid, KVItem } from '$lib/components/detail';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';

	let {
		download,
		allDownloads = [],
		destinationName = null,
		providerName = null,
		onBack,
		onSelectSibling = () => {}
	}: {
		download: Download;
		allDownloads?: Download[];
		destinationName?: string | null;
		providerName?: string | null;
		onBack: () => void;
		onSelectSibling?: (id: string) => void;
	} = $props();

	let busy = $state(false);

	const modelType = $derived(modelTypeFromPath(download.destination_path));
	const siblings = $derived(
		download.group_id ? allDownloads.filter((d) => d.group_id === download.group_id) : []
	);
	const rowIcon = $derived(siblings.length > 1 ? 'layers' : download.type === 'media' ? 'image' : 'cube');
	const progress = $derived(Math.round(download.progress * 100));
	const subtitleParts = $derived(
		[download.type.replace('_', ' ').toUpperCase(), modelType ? modelType.toUpperCase() : null].filter(
			(part): part is string => !!part
		)
	);
	const duration = $derived.by(() => {
		if (!download.started_at || !download.completed_at) return null;
		const startedMs = parseServerDate(download.started_at)?.getTime() ?? NaN;
		const completedMs = parseServerDate(download.completed_at)?.getTime() ?? NaN;
		const ms = completedMs - startedMs;
		return ms > 0 ? Math.round(ms / 1000) : null;
	});
	const queuePosition = $derived.by(() => {
		if (download.status !== 'pending') return null;
		const ordered = allDownloads
			.filter((d) => d.status === 'pending')
			.slice()
			.sort((a, b) => (a.created_at ?? '').localeCompare(b.created_at ?? ''));
		const index = ordered.findIndex((d) => d.id === download.id);
		return index >= 0 ? index + 1 : null;
	});

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
			message: `Cancel "${download.filename}"?`,
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
			message: `Remove "${download.filename}" from history?`,
			variant: 'danger'
		});
		if (ok) void withBusy(() => downloadStore.deleteDownload(download.id));
	}
</script>

<div class="flex h-full min-h-0 flex-col">
	<DetailHeader title={download.filename} icon={rowIcon} backLabel="Downloads" {onBack}>
		{#snippet chips()}
			<Badge variant={statusBadgeVariant(download.status)} size="sm" class="uppercase tracking-wide">
				{statusLabel(download.status)}
			</Badge>
		{/snippet}
		{#snippet subtitle()}
			{#each subtitleParts as part, i}
				{#if i > 0}<span class="text-line-hover">·</span>{/if}
				<span>{part}</span>
			{/each}
			{#if destinationName}
				<span class="text-line-hover">·</span>
				<span class="inline-flex items-center gap-1 uppercase">
					<Icon name="server" className="w-2.5 h-2.5" />
					{destinationName}
				</span>
			{/if}
		{/snippet}
		{#snippet actions()}
			{#if download.status === 'downloading'}
				<Tooltip text="Pause"><IconButton icon="pause" label="Pause" size="sm" disabled={busy} onclick={handlePause} /></Tooltip>
				<Tooltip text="Cancel"><IconButton icon="close" label="Cancel" size="sm" class="text-danger hover:bg-danger/10" disabled={busy} onclick={handleCancel} /></Tooltip>
			{:else if download.status === 'paused'}
				<Button variant="primary" size="sm" icon="play" loading={busy} disabled={busy} onclick={handleResume}>Resume</Button>
				<Tooltip text="Cancel"><IconButton icon="close" label="Cancel" size="sm" class="text-danger hover:bg-danger/10" disabled={busy} onclick={handleCancel} /></Tooltip>
			{:else if download.status === 'pending'}
				<Tooltip text="Cancel"><IconButton icon="close" label="Cancel" size="sm" class="text-danger hover:bg-danger/10" disabled={busy} onclick={handleCancel} /></Tooltip>
			{:else if download.status === 'failed'}
				<Button variant="primary" size="sm" icon="refresh" loading={busy} disabled={busy} onclick={handleRetry}>Retry</Button>
				<Tooltip text="Remove from history"><IconButton icon="trash" label="Remove from history" size="sm" class="text-danger hover:bg-danger/10" disabled={busy} onclick={handleRemove} /></Tooltip>
			{:else}
				<Button variant="primary" size="sm" icon="download" loading={busy} disabled={busy} onclick={handleDownloadAgain}>Download again</Button>
				<Tooltip text="Remove from history"><IconButton icon="trash" label="Remove from history" size="sm" class="text-danger hover:bg-danger/10" disabled={busy} onclick={handleRemove} /></Tooltip>
			{/if}
		{/snippet}
	</DetailHeader>

	<DetailBody>
		<DetailLayout>
			{#snippet main()}
				{#if download.status === 'pending'}
					<DetailSection label="Queue">
						<KVGrid>
							<KVItem label="Added" mono>{downloadStore.formatTimestamp(download.created_at)}</KVItem>
							<KVItem label="Queue position" mono>{queuePosition ?? '—'}</KVItem>
						</KVGrid>
					</DetailSection>
				{:else if download.status === 'downloading' || download.status === 'paused'}
					<DetailSection label="Transfer">
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
					</DetailSection>
				{:else if download.status === 'completed'}
					<DetailSection label="Result">
						<KVItem label="Destination path" full mono>
							<div class="flex items-center gap-2 bg-surface-2 border border-line rounded px-2.5 py-1.5">
								<span class="flex-1 min-w-0 overflow-x-auto whitespace-nowrap">{download.destination_path}</span>
								<CopyButton text={download.destination_path} title="Copy path" />
							</div>
						</KVItem>
						{#if download.type === 'model'}
							<div class="flex items-center gap-2 px-3 py-2.5 mt-3 rounded bg-success/10 border border-success/25">
								<Icon name="check-circle" className="w-3.5 h-3.5 text-success flex-shrink-0" />
								<span class="text-xs text-fg flex-1">
									Indexed into model catalog{#if modelType} as <b class="font-semibold">{modelType}</b>{/if}
								</span>
							</div>
						{/if}
						{#if download.tags && download.tags.length > 0}
							<div class="mt-3 flex flex-wrap gap-1.5">
								{#each download.tags as tag}
									<span class="text-xs text-fg-muted bg-surface-2 border border-line rounded px-2 py-0.5">{tag}</span>
								{/each}
							</div>
						{/if}
					</DetailSection>
				{:else if download.status === 'failed'}
					<DetailSection label="Error">
						<div class="font-mono text-xs leading-relaxed text-danger bg-danger/10 border border-danger/25 rounded px-3 py-2.5 whitespace-pre-wrap break-words">
							{download.error_message || 'Unknown error'}
						</div>
						<div class="flex items-center gap-5 mt-3 flex-wrap">
							<div class="flex flex-col gap-0.5">
								<span class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle">Retry attempts</span>
								<span class="text-sm text-fg">{download.retry_count}</span>
							</div>
						</div>
					</DetailSection>
				{:else}
					<DetailSection label="Transfer at cancel">
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
					</DetailSection>
				{/if}

				{#if siblings.length > 1}
					<DetailSection label="Batch — {siblings.length} files">
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
					</DetailSection>
				{/if}
			{/snippet}

			{#snippet aside()}
				<DetailSection label="Source">
					<KVGrid>
						{#if providerName}
							<KVItem label="Provider">{providerName}</KVItem>
						{/if}
						{#if download.url}
							<KVItem label="Source URL" full mono>
								<div class="flex items-center gap-2 bg-surface-2 border border-line rounded px-2.5 py-1.5">
									<span class="flex-1 min-w-0 overflow-x-auto whitespace-nowrap">{download.url}</span>
									<CopyButton text={download.url} title="Copy URL" />
								</div>
							</KVItem>
						{/if}
					</KVGrid>
				</DetailSection>

				<DetailSection label="Destination">
					<KVGrid>
						<KVItem label="Backend">
							{#if destinationName}
								<span class="inline-flex items-center gap-1 text-xs font-mono uppercase text-fg-subtle bg-surface-2 border border-line rounded px-1.5 py-0.5">
									<Icon name="server" className="w-2.5 h-2.5" />
									{destinationName}
								</span>
							{:else}
								This machine
							{/if}
						</KVItem>
						<KVItem label="Size" mono>
							{download.total_bytes ? downloadStore.formatBytes(download.total_bytes) : 'Unknown'}
						</KVItem>
						{#if download.checksum_sha256}
							<KVItem label="SHA-256" full>
								<span class="inline-flex items-center gap-1.5">
									<span class="font-mono text-xs text-fg-muted">
										{download.checksum_sha256.slice(0, 10)}…{download.checksum_sha256.slice(-8)}
									</span>
									<CopyButton text={download.checksum_sha256} title="Copy checksum" />
								</span>
							</KVItem>
						{/if}
					</KVGrid>
				</DetailSection>

				<DetailSection label="Timestamps">
					<KVGrid>
						{#if download.created_at}<KVItem label="Queued" mono>{downloadStore.formatTimestamp(download.created_at)}</KVItem>{/if}
						{#if download.started_at}<KVItem label="Started" mono>{downloadStore.formatTimestamp(download.started_at)}</KVItem>{/if}
						{#if download.completed_at}<KVItem label="Completed" mono>{downloadStore.formatTimestamp(download.completed_at)}</KVItem>{/if}
						{#if duration !== null}<KVItem label="Duration" mono>{duration}s</KVItem>{/if}
					</KVGrid>
				</DetailSection>

				<DetailSection label="Retries">
					<KVItem label="Attempts" mono>{download.retry_count}</KVItem>
				</DetailSection>
			{/snippet}
		</DetailLayout>
	</DetailBody>
</div>

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
</style>
