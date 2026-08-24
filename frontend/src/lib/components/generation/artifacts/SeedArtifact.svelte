<script lang="ts">
	import { copyText } from '$lib/utils/clipboard';

	export let artifact: { artifact_data: { seed: number } };
	export let onSeedClick: ((seed: number) => void) | undefined = undefined;
</script>

<div class="flex items-center gap-3">
	<button
		class="px-3 py-1.5 bg-surface-3 text-fg font-mono tabular-nums font-semibold hover:bg-line-hover transition-colors flex items-center gap-1"
		on:click={() => onSeedClick?.(artifact.artifact_data.seed)}
		title="Click to use this seed"
	>
		<span class="text-fg-subtle">#</span>
		{artifact.artifact_data.seed}
	</button>
	<button
		class="p-2 hover:bg-surface-2 transition-colors"
		on:click={() => copyText(artifact.artifact_data.seed.toString())}
		title="Copy seed to clipboard"
	>
		<svg class="w-4 h-4 text-fg-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
		</svg>
	</button>
</div>
