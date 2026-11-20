<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { KIT_KIND_ICONS, KIT_KIND_PLURALS, presentKinds } from '$lib/kits/kitCounts';
	import { layoutPreview, previewHeight, visualItems } from '$lib/kits/kitPreview';
	import type { KitCounts, KitItem } from '$lib/kits/types';

	let { items, counts }: { items: KitItem[]; counts: KitCounts } = $props();

	let width = $state(0);
	let measured = $state<Record<string, number>>({});
	let failed = $state<Record<string, true>>({});

	let visuals = $derived(visualItems(items).filter((item) => !failed[item.id]));
	let layout = $derived(layoutPreview(visuals, width, measured));
	let kinds = $derived(presentKinds(counts));

	function measure(id: string, event: Event) {
		const img = event.currentTarget as HTMLImageElement;
		if (img.naturalWidth > 0 && img.naturalHeight > 0) measured[id] = img.naturalWidth / img.naturalHeight;
	}
</script>

<div bind:clientWidth={width} class="bg-surface-2" data-kit-preview>
	{#if visuals.length > 0}
		<div class="flex flex-col items-center justify-center gap-0.5" style="height:{previewHeight(width)}px">
			{#each layout.rows as row, index (index)}
				<div class="flex gap-0.5" style="height:{row.height}px">
					{#each row.tiles as tile (tile.item.id)}
						<span class="relative block flex-none overflow-hidden" style="width:{tile.width}px;height:{tile.height}px">
							<img
								src={tile.item.thumbnail_url ?? tile.item.url}
								alt=""
								loading="lazy"
								class="h-full w-full object-contain"
								onload={(event) => measure(tile.item.id, event)}
								onerror={() => (failed[tile.item.id] = true)}
							/>
							{#if tile.item.kind === 'video'}
								<span class="absolute bottom-1 left-1 flex h-5 w-5 items-center justify-center rounded bg-black/70 text-white">
									<Icon name="play" className="h-3 w-3" />
								</span>
							{/if}
						</span>
					{/each}
				</div>
			{/each}
		</div>
	{:else}
		<div class="flex h-12 items-center gap-3 px-3 text-fg-muted" data-kit-preview-empty>
			{#each kinds as kind (kind)}
				<span class="inline-flex items-center gap-1.5 text-sm">
					<Icon name={KIT_KIND_ICONS[kind]} className="h-4 w-4 text-fg-subtle" strokeWidth={1.75} />
					<span class="font-mono tabular-nums">{counts[kind]}</span>
					<span>{KIT_KIND_PLURALS[kind]}</span>
				</span>
			{:else}
				<Icon name="layers" className="h-4 w-4 text-fg-subtle" strokeWidth={1.5} />
				<span class="text-sm text-fg-subtle">Empty kit</span>
			{/each}
		</div>
	{/if}
</div>
