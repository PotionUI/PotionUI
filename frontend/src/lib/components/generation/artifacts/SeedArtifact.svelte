<script lang="ts">
	import { CopyButton } from '$lib/components/ui';

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
	<CopyButton text={artifact.artifact_data.seed.toString()} ariaLabel="Copy seed to clipboard" />
</div>
