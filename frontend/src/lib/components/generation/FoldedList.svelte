<script lang="ts" generics="T">
	import type { Snippet } from 'svelte';
	import FoldButton from './FoldButton.svelte';
	import { detailsPrefs, loadDetailsPrefs, setDetailsPref } from './detailsPrefs';
	import { foldList } from './listFold';

	let {
		items,
		above,
		show,
		id,
		item,
		class: className = ''
	}: {
		items: T[];
		above: number;
		show: number;
		id: string;
		item: Snippet<[T, number]>;
		class?: string;
	} = $props();

	loadDetailsPrefs();

	const uid = $props.id();
	const listId = `${uid}-list`;

	let expanded = $derived($detailsPrefs[`list:${id}`] ?? false);
	let fold = $derived(foldList(items, above, show, expanded));
</script>

<div id={listId} class={className}>
	{#each fold.visible as entry, index}
		{@render item(entry, index)}
	{/each}
</div>
{#if fold.foldable}
	<div class="px-3 pt-1">
		<FoldButton
			label={expanded ? 'Show less' : `Show all (${fold.total})`}
			{expanded}
			controls={listId}
			onToggle={() => setDetailsPref(`list:${id}`, !expanded)}
		/>
	</div>
{/if}
