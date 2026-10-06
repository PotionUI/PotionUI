<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import PresetCoverTile from '../../../routes/admin/components/presets/PresetCoverTile.svelte';
	import type { Lead } from './types';

	let { lead, size = 40 }: { lead: Lead; size?: number } = $props();

	let errored = $state(false);

	$effect(() => {
		void lead;
		errored = false;
	});

	const box = $derived(`width: ${size}px; height: ${size}px;`);
</script>

{#if lead.type === 'cover'}
	<PresetCoverTile
		presetId={lead.id}
		presetName={lead.name}
		cover={lead.src}
		category={lead.category ?? null}
		zoom={false}
		class="h-10 w-10 flex-shrink-0 rounded border border-line-strong"
	/>
{:else if lead.type === 'thumb'}
	<span class="flex flex-shrink-0 items-center justify-center overflow-hidden rounded border border-line-strong bg-surface-2 text-fg-subtle" style={box}>
		{#if lead.src && !errored}
			<img src={lead.src} alt="" class="h-full w-full object-cover" loading="lazy" onerror={() => (errored = true)} />
		{:else}
			<Icon name={lead.icon ?? 'image'} className="h-4 w-4" strokeWidth={1.6} />
		{/if}
	</span>
{:else if lead.type === 'avatar'}
	<span
		class="flex flex-shrink-0 items-center justify-center overflow-hidden rounded-full border border-line-strong bg-surface-3 font-mono text-xs font-semibold text-fg-muted"
		style={box}
	>
		{#if lead.src && !errored}
			<img src={lead.src} alt="" class="h-full w-full object-cover" onerror={() => (errored = true)} />
		{:else}
			{lead.text}
		{/if}
	</span>
{:else}
	<span class="flex flex-shrink-0 items-center justify-center rounded border border-line-strong bg-surface-2 text-fg-subtle" style={box}>
		<Icon name={lead.name} className="h-4 w-4" strokeWidth={1.6} />
	</span>
{/if}
