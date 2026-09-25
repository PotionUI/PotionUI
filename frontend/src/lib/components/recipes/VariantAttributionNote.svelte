<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import type { VariantAttribution } from './variantAttribution';

	let { attribution }: { attribution: VariantAttribution } = $props();
</script>

<div
	class="flex items-start gap-2 rounded border border-line bg-surface-2 px-3 py-2 text-sm text-fg-subtle"
	data-variant-attribution
>
	<Icon name="info" className="w-3.5 h-3.5 shrink-0 mt-0.5" />
	<span>
		Free to download thanks to Hugging Face and {attribution.uploader}, who uploaded this model.
		{#if attribution.source_url}
			<a href={attribution.source_url} target="_blank" rel="noreferrer" class="text-signal hover:underline">
				{attribution.repo_id ?? attribution.source_url}
			</a>
		{/if}
	</span>
</div>
