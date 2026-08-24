<script lang="ts">
	export let artifact: {
		artifact_data: {
			name?: string;
			diff: string | Array<[string, string]>;
			negative_applied?: boolean;
		};
	};

	$: inert = artifact.artifact_data.negative_applied === false;
</script>

<div class="space-y-3" class:opacity-60={inert}>
	<div class="flex items-center gap-2">
		<div class="p-1 bg-surface-3">
			<svg class="w-3.5 h-3.5 text-fg" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
			</svg>
		</div>
		<h5 class="font-bold text-sm text-fg">{artifact.artifact_data.name || 'Text Diff'}</h5>
		{#if inert}
			<span class="text-xs text-fg-subtle">Not applied at current settings</span>
		{/if}
	</div>
	<div class="bg-surface-2/50 border border-line/60 p-3">
		<div class="text-sm font-mono leading-relaxed">
			{#if Array.isArray(artifact.artifact_data.diff)}
				<div class="space-y-0.5">
					{#each artifact.artifact_data.diff as [text, operation]}
						<span
							class="{operation === '+'
								? 'bg-success/10 text-success px-1 py-0.5 border-l-2 border-success'
								: operation === '-'
									? 'bg-danger/10 text-danger px-1 py-0.5 border-l-2 border-danger'
									: 'text-fg-muted'}"
						>
							{text}
						</span>
					{/each}
				</div>
			{:else}
				<pre class="whitespace-pre-wrap text-sm text-fg-muted">{artifact.artifact_data.diff}</pre>
			{/if}
		</div>
	</div>
</div>
