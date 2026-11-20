<script lang="ts">
	import { liveClock } from '$lib/generation/live/liveDefault';
	import { elapsedSeconds, formatElapsed } from '$lib/generation/live/liveView';
	import type { GenerationHistoryItem } from '$lib/types/history';

	let {
		generation,
		class: className = ''
	}: {
		generation: Pick<GenerationHistoryItem, 'started_at' | 'created_at'>;
		class?: string;
	} = $props();
</script>

<span class="font-mono tabular-nums whitespace-nowrap {className}" data-live-tile-elapsed>{formatElapsed(elapsedSeconds(generation, $liveClock))}</span>
