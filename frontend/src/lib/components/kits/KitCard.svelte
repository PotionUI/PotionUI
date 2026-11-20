<script lang="ts">
	import { Badge } from '$lib/components/ui';
	import KitKindCounts from './KitKindCounts.svelte';
	import KitCardPreview from './KitCardPreview.svelte';
	import type { Kit } from '$lib/kits/types';

	let { kit, onOpen }: { kit: Kit; onOpen: (kit: Kit) => void } = $props();
</script>

<article
	class="group flex flex-col overflow-hidden rounded-lg border border-line bg-surface-1 shadow-raised transition-colors hover:border-line-strong"
	data-kit-card={kit.id}
>
	<button type="button" class="flex w-full flex-1 flex-col text-left" aria-label="Open {kit.name}" onclick={() => onOpen(kit)}>
		<KitCardPreview items={kit.preview_items} counts={kit.counts} />
		<div class="mt-auto space-y-2 border-t border-line p-3">
			<div class="flex items-center justify-between gap-2">
				<h3 class="min-w-0 truncate text-base font-semibold text-fg">{kit.name}</h3>
				{#if kit.label}<Badge size="sm">{kit.label}</Badge>{/if}
			</div>
			<div class="flex items-center justify-between gap-2 text-fg-muted">
				<KitKindCounts counts={kit.counts} />
				<span class="font-mono text-sm tabular-nums text-fg-subtle">{kit.item_count}</span>
			</div>
		</div>
	</button>
</article>
