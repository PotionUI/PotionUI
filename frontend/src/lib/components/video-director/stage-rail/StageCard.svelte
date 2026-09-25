<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		title,
		actions,
		media,
		children,
		class: className = ''
	}: {
		title?: Snippet;
		actions?: Snippet;
		media?: Snippet;
		children?: Snippet;
		class?: string;
	} = $props();
</script>

<div class="stage-card rounded-lg border border-line bg-surface-1 p-3 {className}">
	{#if title || actions}
		<div class="mb-3 flex items-center justify-between gap-2">
			<div class="flex min-w-0 items-center gap-2">
				{@render title?.()}
			</div>
			{#if actions}
				<div class="flex shrink-0 items-center gap-1">
					{@render actions()}
				</div>
			{/if}
		</div>
	{/if}

	{#if media}
		<div class="stage-card-split">
			<div class="stage-card-media">
				{@render media()}
			</div>
			<div class="stage-card-controls flex min-w-0 flex-col gap-3">
				{@render children?.()}
			</div>
		</div>
	{:else}
		{@render children?.()}
	{/if}
</div>

<style>
	.stage-card {
		container-type: inline-size;
		container-name: stage-card;
	}

	.stage-card-split {
		display: grid;
		grid-template-columns: 1fr;
		gap: 12px;
	}

	@container stage-card (min-width: 30rem) {
		.stage-card-split {
			grid-template-columns: 16rem minmax(0, 1fr);
			align-items: stretch;
		}
	}

	.stage-card-media {
		display: flex;
		min-width: 0;
		min-height: 11rem;
	}

	.stage-card-media > :global(*) {
		flex: 1 1 auto;
		min-width: 0;
	}

	.stage-card-controls {
		min-width: 0;
		padding-right: 2px;
	}
</style>
