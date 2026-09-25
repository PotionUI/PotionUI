<script lang="ts">
	import { Badge, Button } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { formatBytes } from '$lib/utils/format';
	import type { SetupConsentVariant } from '$lib/services/api/setup';
	import type { ModelDownloadState } from '$lib/utils/modelDownloadState';

	let {
		variant,
		suggested = false,
		reason = null,
		current = false,
		showUploader = false,
		download,
		onDownload
	}: {
		variant: SetupConsentVariant;
		suggested?: boolean;
		reason?: string | null;
		current?: boolean;
		showUploader?: boolean;
		download: ModelDownloadState;
		onDownload: () => void;
	} = $props();

	const busy = $derived(download.phase === 'starting' || download.phase === 'polling');
	const done = $derived(variant.installed || download.phase === 'completed');
	const percent = $derived(Math.round((download.progress ?? 0) * 100));
</script>

<div
	class="flex flex-col gap-1.5 px-3 py-2.5 {suggested ? 'border-l-2 border-l-signal' : ''}"
	data-slot-variant={variant.id}
>
	<div class="flex items-center gap-2 min-w-0">
		<span class="text-sm font-semibold text-fg truncate">{variant.label}</span>
		{#if variant.precision}
			<Badge class="font-mono shrink-0">{variant.precision}</Badge>
		{/if}
		{#if suggested}
			<Badge variant="signal" class="shrink-0">Suggested</Badge>
		{/if}
		{#if current}
			<Badge variant="neutral" class="shrink-0">This file</Badge>
		{/if}
		{#if variant.note}
			<Tooltip text={variant.note} wrapperClass="flex shrink-0">
				<Icon name="info" className="w-3.5 h-3.5 text-fg-subtle" />
			</Tooltip>
		{/if}
		<span class="flex-1"></span>
		{#if variant.size_bytes != null}
			<span class="font-mono tabular-nums text-sm text-fg-muted shrink-0">{formatBytes(variant.size_bytes)}</span>
		{/if}
		{#if done}
			<Badge variant="success" class="shrink-0">Installed</Badge>
		{:else if download.phase !== 'forbidden'}
			<Button
				size="xs"
				variant={suggested ? 'primary' : 'secondary'}
				icon="download"
				loading={busy}
				disabled={busy}
				onclick={(event) => {
					event.stopPropagation();
					onDownload();
				}}
			>
				Download
			</Button>
		{/if}
	</div>
	{#if showUploader && variant.uploader}
		<span class="text-sm text-fg-subtle" data-slot-variant-uploader>Uploaded by {variant.uploader}</span>
	{/if}
	{#if suggested && reason}
		<span class="text-sm text-signal" data-slot-variant-reason>{reason}</span>
	{/if}
	{#if variant.gated}
		<div class="flex items-center gap-1 text-sm text-warning">
			<span>Licence required</span>
			{#if variant.license_url}
				<a href={variant.license_url} target="_blank" rel="noreferrer" class="underline decoration-dotted">licence</a>
			{/if}
		</div>
	{/if}
	{#if busy}
		<div class="flex items-center gap-2">
			<div class="h-1 flex-1 overflow-hidden rounded-sm bg-surface-3">
				<div class="h-full bg-signal-solid transition-all duration-300" style="width: {percent}%"></div>
			</div>
			<span class="font-mono tabular-nums text-xs text-fg-muted">{download.progress != null ? `${percent}%` : '…'}</span>
		</div>
	{:else if download.phase === 'failed'}
		<span class="text-sm text-danger">{download.error || 'Download failed'}</span>
	{:else if download.phase === 'forbidden'}
		<span class="text-sm text-fg-subtle">Admin permission required to download this model.</span>
	{/if}
</div>
