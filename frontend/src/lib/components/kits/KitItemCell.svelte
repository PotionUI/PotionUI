<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { KIT_KIND_ICONS } from '$lib/kits/kitCounts';
	import type { KitItem } from '$lib/kits/types';

	let { item, big = false }: { item: KitItem; big?: boolean } = $props();

	let src = $derived(item.thumbnail_url ?? (item.kind === 'image' ? item.url : null));
	let failed = $state(false);
</script>

{#if (item.kind === 'image' || item.kind === 'video') && src && !failed && !item.missing}
	<span class="relative block h-full w-full overflow-hidden bg-surface-2">
		<img {src} alt="" loading="lazy" class="h-full w-full object-cover" onerror={() => (failed = true)} />
		{#if item.kind === 'video'}
			<span class="absolute bottom-1 left-1 flex h-5 w-5 items-center justify-center rounded bg-black/70 text-white">
				<Icon name="play" className="h-3 w-3" />
			</span>
		{/if}
	</span>
{:else}
	<span class="flex h-full w-full items-center justify-center bg-surface-2 px-2 text-fg-muted">
		<Icon name={item.missing ? 'warning' : KIT_KIND_ICONS[item.kind]} className={big ? 'h-6 w-6' : 'h-4 w-4'} strokeWidth={1.75} />
	</span>
{/if}
