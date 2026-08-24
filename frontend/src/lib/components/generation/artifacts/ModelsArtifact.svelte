<script lang="ts">
	export let artifact: { artifact_data: { models: Array<{ name: string; type: string; weight?: number }> } };
</script>

<div class="space-y-2">
	{#each artifact.artifact_data.models as model}
		<div class="flex items-center gap-2 flex-wrap p-2 bg-surface-2/50">
			<span class="px-2 py-1 bg-surface-3 text-fg text-xs font-semibold flex items-center gap-1">
				<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
				</svg>
				{model.name}
			</span>
			<span class="px-2 py-1 bg-surface-2 text-fg-muted text-xs font-medium border border-line-strong/50">
				{model.type}
			</span>
			{#if model.weight}
				<span class="px-2 py-1 bg-success/10 text-success text-xs font-bold font-mono tabular-nums border border-success/25">
					{model.weight}x
				</span>
			{/if}
		</div>
	{/each}
</div>
