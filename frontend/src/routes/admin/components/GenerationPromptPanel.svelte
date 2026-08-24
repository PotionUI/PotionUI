<script lang="ts">
	let { promptTemplate }: { promptTemplate: { positive: string; negative: string } } = $props();
</script>

<div class="bg-surface-1 border border-line rounded-lg p-4 sm:p-5 space-y-3">
	<h3 class="text-sm font-medium text-fg">Prompt template</h3>

	<div>
		<p class="text-2xs font-mono uppercase tracking-[0.07em] text-fg-subtle mb-1">Positive</p>
		<pre class="text-xs font-mono text-fg-muted leading-relaxed whitespace-pre-wrap break-words bg-surface-2/60 rounded p-3">{promptTemplate.positive || '-'}</pre>
	</div>

	{#if promptTemplate.negative}
		<div>
			<p class="text-2xs font-mono uppercase tracking-[0.07em] text-fg-subtle mb-1">Negative</p>
			<pre class="text-xs font-mono text-fg-muted leading-relaxed whitespace-pre-wrap break-words bg-surface-2/60 rounded p-3">{promptTemplate.negative}</pre>
		</div>
	{/if}
</div>
