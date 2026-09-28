<script lang="ts">
	import { formatBytes, formatDate } from './formatters';
	import MetadataCard, { type MetadataRow } from './MetadataCard.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Badge } from '$lib/components/ui';
	import { KVItem } from '$lib/components/detail';
	import type { ModelLocation } from '$lib/types/models';

	export let filename: string = '';
	export let location: ModelLocation | null | undefined = undefined;
	export let copies: number = 0;
	export let sha256: string | null | undefined = undefined;
	export let fileSize: number | null | undefined = undefined;
	export let indexedAt: string | null | undefined = undefined;
	export let bare: boolean = false;

	$: rows = [
		{ label: 'Filename', value: filename, copyValue: filename, title: filename, copyLabel: 'Copy filename' },
		...(sha256
			? [
					{
						label: 'SHA256',
						value: `${sha256.slice(0, 12)}...`,
						copyValue: sha256,
						title: sha256,
						copyLabel: 'Copy SHA256'
					}
				]
			: []),
		{ label: 'Size', value: formatBytes(fileSize) },
		...(indexedAt ? [{ label: 'Indexed', value: formatDate(indexedAt) }] : [])
	] satisfies MetadataRow[];
</script>

{#snippet locationRow(isBare: boolean)}
	{#if isBare}
		<KVItem label="Location" full>
			{#if location}
				<div class="flex items-center gap-1.5 flex-wrap">
					<Tooltip text={location.path ?? 'Path unavailable'}>
						<code class="text-xs font-mono bg-surface-3 px-1.5 py-0.5 rounded truncate max-w-[280px] text-fg">
							{location.logical_path}
						</code>
					</Tooltip>
					<Badge variant="neutral" size="sm">{location.root_label}</Badge>
					{#if copies > 1}
						<Badge variant="info" size="sm">{copies} copies</Badge>
					{/if}
				</div>
			{:else}
				<span class="text-xs text-fg-subtle italic">Not stored locally</span>
			{/if}
		</KVItem>
	{:else}
		<div class="flex items-center justify-between py-1 gap-2">
			<span class="text-fg-muted flex-shrink-0">Location</span>
			{#if location}
				<div class="flex items-center gap-1.5 min-w-0 justify-end flex-wrap">
					<Tooltip text={location.path ?? 'Path unavailable'}>
						<code class="text-xs font-mono bg-surface-3 px-1.5 py-0.5 rounded truncate max-w-[180px] text-fg">
							{location.logical_path}
						</code>
					</Tooltip>
					<Badge variant="neutral" size="sm">{location.root_label}</Badge>
					{#if copies > 1}
						<Badge variant="info" size="sm">{copies} copies</Badge>
					{/if}
				</div>
			{:else}
				<span class="text-xs text-fg-subtle italic">Not stored locally</span>
			{/if}
		</div>
	{/if}
{/snippet}

<MetadataCard icon="shield" title="Technical Details" {rows} {bare} extra={locationRow} />
