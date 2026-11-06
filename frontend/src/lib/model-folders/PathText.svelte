<script lang="ts">
	import Tooltip from '$lib/components/Tooltip.svelte';

	let {
		text,
		full,
		class: className = '',
		muted = true
	}: { text: string; full?: string; class?: string; muted?: boolean } = $props();
</script>

<Tooltip text={full ?? text} position="top" truncateOnly={!full} wrapperClass="flex min-w-0 max-w-full">
	<span class="block min-w-0 max-w-full truncate font-mono text-[12px] {muted ? 'text-fg-subtle' : 'text-fg-muted'} {className}">{text}</span>
</Tooltip>
