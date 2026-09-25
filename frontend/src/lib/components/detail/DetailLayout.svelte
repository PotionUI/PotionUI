<script lang="ts">
	import type { Snippet } from 'svelte';
	import { detailLayoutColumnsClass } from './detailLayout';

	let {
		lead,
		main,
		aside
	}: {
		lead?: Snippet;
		main: Snippet;
		aside?: Snippet;
	} = $props();
</script>

<div class="detail-layout">
	{#if lead}
		<div class="detail-layout-lead">{@render lead()}</div>
	{/if}
	<div class={detailLayoutColumnsClass(!!aside)}>
		<div class="space-y-4 min-w-0">{@render main()}</div>
		{#if aside}
			<div class="space-y-4 min-w-0" data-detail-layout-aside>{@render aside()}</div>
		{/if}
	</div>
</div>

<style>
	.detail-layout {
		container-type: inline-size;
		container-name: detail-layout;
	}

	.detail-layout-lead {
		margin-bottom: 1.25rem;
	}

	.detail-layout-columns {
		display: grid;
		grid-template-columns: 1fr;
		align-items: start;
		gap: 1.25rem;
	}

	@container detail-layout (min-width: 42rem) {
		.detail-layout-columns:not(.detail-layout-columns--main-only) {
			grid-template-columns: minmax(0, 1fr) 18rem;
		}
	}

	@container detail-layout (min-width: 88rem) {
		.detail-layout-columns:not(.detail-layout-columns--main-only) {
			grid-template-columns: minmax(0, 1fr) 20rem;
		}
	}
</style>
