<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { KIT_KIND_ICONS } from '$lib/kits/kitCounts';
	import type { KitItem } from '$lib/kits/types';

	let { item, skipped = false }: { item: KitItem; skipped?: boolean } = $props();

	let src = $derived(item.thumbnail_url ?? (item.kind === 'image' ? item.url : null));
	let struck = $derived(item.missing);
</script>

<span
	class="inline-flex h-8 max-w-full items-center gap-1.5 rounded border py-0.5 pl-0.5 pr-2 font-mono text-sm {skipped || struck
		? 'border-warning/40 bg-warning/10 text-warning'
		: 'border-line bg-surface-2/60 text-fg-muted'}"
	data-kit-item-chip={item.name}
	data-skipped={skipped || struck ? '' : undefined}
>
	{#if src && !item.missing && (item.kind === 'image' || item.kind === 'video')}
		<img {src} alt="" class="h-6 w-6 flex-shrink-0 rounded-sm object-cover" loading="lazy" />
	{:else}
		<span class="flex h-6 w-6 flex-shrink-0 items-center justify-center text-fg-subtle">
			<Icon name={KIT_KIND_ICONS[item.kind]} className="h-3.5 w-3.5" strokeWidth={1.75} />
		</span>
	{/if}
	<span class="truncate">{item.name}</span>
	{#if skipped || struck}
		<Icon name="warning" className="h-3 w-3 flex-shrink-0" />
		<span class="sr-only">skipped</span>
	{/if}
</span>
