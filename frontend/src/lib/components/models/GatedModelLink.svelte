<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { gatedPageUrl, type GatedSource } from '$lib/models/gatedLink';

	let {
		source,
		label = 'Open on Hugging Face',
		class: className = ''
	}: { source: GatedSource | null | undefined; label?: string; class?: string } = $props();

	const href = $derived(gatedPageUrl(source));
</script>

{#if href}
	<a
		{href}
		target="_blank"
		rel="noopener noreferrer"
		class="inline-flex shrink-0 items-center gap-1 text-xs text-signal hover:underline {className}"
		data-gated-link
		onclick={(event) => event.stopPropagation()}
	>
		<span>{label}</span>
		<Icon name="external-link" className="w-3 h-3" />
	</a>
{/if}
