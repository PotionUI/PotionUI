<script lang="ts" generics="T extends { id: string; name: string; color: string }">
	import Icon from '$lib/components/Icon.svelte';

	// First-N colored tag toggles + clear button, shared by the history and
	// model library quick-filter rows. Tags past `visibleLimit` are handed to
	// the caller via the "overflow" slot - history renders a searchable
	// dropdown there, models just shows a "+N" count.
	export let tags: T[];
	export let selectedIds: string[];
	export let onToggle: (tagId: string) => void;
	export let onClear: () => void;
	export let visibleLimit = 15;

	$: visibleTags = tags.slice(0, visibleLimit);
	$: overflowTags = tags.slice(visibleLimit);
</script>

{#if tags.length > 0}
	<div class="h-9 px-6 flex items-center gap-3 overflow-x-auto no-scrollbar bg-surface-2/50 border-t border-line">
		<Icon name="tag" className="w-3.5 h-3.5 text-fg-subtle flex-shrink-0" />
		{#each visibleTags as tag (tag.id)}
			<button
				class="flex-shrink-0 inline-flex items-center gap-1.5 px-2 py-1 rounded text-xs font-medium transition-all"
				style="{selectedIds.includes(tag.id) ? `background-color: ${tag.color}20; color: ${tag.color}; box-shadow: 0 0 0 1px ${tag.color}40;` : 'color: rgb(var(--fg-subtle));'}"
				on:click={() => onToggle(tag.id)}
				on:mouseenter={(e) => { if (!selectedIds.includes(tag.id)) e.currentTarget.style.color = tag.color; }}
				on:mouseleave={(e) => { if (!selectedIds.includes(tag.id)) e.currentTarget.style.color = 'rgb(var(--fg-subtle))'; }}
			>
				<span class="w-2 h-2 rounded-full flex-shrink-0" style="background-color: {tag.color}"></span>{tag.name}
			</button>
		{/each}

		<slot name="overflow" {overflowTags} />

		{#if selectedIds.length > 0}
			<div class="flex-shrink-0 w-px h-4 bg-line-strong"></div>
			<button class="flex-shrink-0 text-xs text-fg-subtle hover:text-fg-muted transition-colors" on:click={onClear}>
				Clear filters
			</button>
		{/if}
	</div>
{/if}
