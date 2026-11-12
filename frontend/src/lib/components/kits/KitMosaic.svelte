<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import KitItemCell from './KitItemCell.svelte';
	import type { KitItem } from '$lib/kits/types';

	let { items, class: className = 'aspect-[4/3]' }: { items: KitItem[]; class?: string } = $props();

	let visible = $derived(
		[
			...items.filter((item) => item.kind === 'image' || item.kind === 'video'),
			...items.filter((item) => item.kind === 'audio' || item.kind === 'text')
		].slice(0, 3)
	);
</script>

{#if visible.length === 0}
	<div class="{className} flex items-center justify-center overflow-hidden bg-surface-2 text-fg-subtle">
		<Icon name="layers" className="h-6 w-6" strokeWidth={1.5} />
	</div>
{:else if visible.length === 1}
	<div class="{className} overflow-hidden bg-surface-2">
		<KitItemCell item={visible[0]} big />
	</div>
{:else if visible.length === 2}
	<div class="{className} grid grid-cols-2 gap-px overflow-hidden bg-line">
		{#each visible as item (item.id)}
			<div class="min-h-0 overflow-hidden"><KitItemCell {item} /></div>
		{/each}
	</div>
{:else}
	<div class="{className} grid grid-cols-3 grid-rows-2 gap-px overflow-hidden bg-line">
		<div class="col-span-2 row-span-2 min-h-0 overflow-hidden"><KitItemCell item={visible[0]} big /></div>
		<div class="min-h-0 overflow-hidden"><KitItemCell item={visible[1]} /></div>
		<div class="min-h-0 overflow-hidden"><KitItemCell item={visible[2]} /></div>
	</div>
{/if}
