<script lang="ts">
	import type { OutputEntry } from '$lib/elixirs/types';
	import { stillSource } from '$lib/elixirs/tiles';

	let { outputs = [], max = 3 }: { outputs?: OutputEntry[]; max?: number } = $props();

	let shown = $derived(outputs.map((entry) => stillSource(entry)).filter((url): url is string => !!url).slice(0, max));
</script>

{#if shown.length > 0}
	<span class="thumbs" aria-hidden="true" data-testid="elixir-thumbs">
		{#each shown as url, index (index)}
			<img src={url} alt="" loading="lazy" />
		{/each}
	</span>
{/if}

<style>
	.thumbs {
		display: flex;
		flex-shrink: 0;
	}

	img {
		width: 24px;
		height: 24px;
		border-radius: 4px;
		object-fit: cover;
		box-shadow: 0 0 0 2px rgb(var(--surface-2));
	}

	img + img {
		margin-left: -8px;
	}
</style>
