<script lang="ts">
	import { highlightSegments, type TextMatcher } from '$lib/utils/textMatch';

	let { text, matcher }: { text: string; matcher: TextMatcher | null } = $props();

	let segments = $derived(highlightSegments(text ?? '', matcher));
</script>

{#each segments as segment, i (i)}{#if segment.match}<mark class="bg-signal/20 text-fg rounded-[2px]">{segment.text}</mark>{:else}{segment.text}{/if}{/each}
