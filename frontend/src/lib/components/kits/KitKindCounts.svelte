<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { KIT_KIND_ICONS, KIT_KIND_PLURALS, presentKinds } from '$lib/kits/kitCounts';
	import type { KitCounts } from '$lib/kits/types';

	let { counts, class: className = '' }: { counts: KitCounts; class?: string } = $props();

	let kinds = $derived(presentKinds(counts));
</script>

<span class="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm {className}">
	{#each kinds as kind (kind)}
		<span class="inline-flex items-center gap-1">
			<Icon name={KIT_KIND_ICONS[kind]} className="h-3.5 w-3.5 text-fg-subtle" strokeWidth={1.75} />
			<span class="font-mono tabular-nums">{counts[kind]}</span>
			<span class="sr-only">{KIT_KIND_PLURALS[kind]}</span>
		</span>
	{/each}
</span>
