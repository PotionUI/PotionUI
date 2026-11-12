<script lang="ts">
	import { Badge } from '$lib/components/ui';

	let { tags, limit = 2 }: { tags: Array<{ id: string; name: string }>; limit?: number } = $props();
</script>

{#if tags.length === 0}
	<span class="text-fg-subtle">—</span>
{:else}
	<div class="flex items-center gap-1 min-w-0" data-testid="row-tags">
		{#each tags.slice(0, limit) as tag (tag.id)}
			<Badge variant="neutral" size="sm" class="max-w-28 truncate">{tag.name}</Badge>
		{/each}
		{#if tags.length > limit}
			<Badge variant="neutral" size="sm" class="font-mono tabular-nums">+{tags.length - limit}</Badge>
		{/if}
	</div>
{/if}
