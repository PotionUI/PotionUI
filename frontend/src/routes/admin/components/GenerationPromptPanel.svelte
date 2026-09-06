<script lang="ts">
	import { DetailSection } from '$lib/components/detail';

	let { promptTemplate }: { promptTemplate: { positive: string; negative: string } } = $props();
</script>

<DetailSection label="Prompt template">
	<div class="space-y-4">
		<div>
			<p class="text-2xs font-mono uppercase tracking-[0.07em] text-fg-subtle mb-1.5">Positive</p>
			<pre class="text-xs font-mono text-fg-muted leading-relaxed whitespace-pre-wrap break-words bg-surface-2/60 border border-line rounded p-3">{promptTemplate.positive || '—'}</pre>
		</div>

		{#if promptTemplate.negative}
			<div>
				<p class="text-2xs font-mono uppercase tracking-[0.07em] text-fg-subtle mb-1.5">Negative</p>
				<pre class="text-xs font-mono text-fg-muted leading-relaxed whitespace-pre-wrap break-words bg-surface-2/60 border border-line rounded p-3">{promptTemplate.negative}</pre>
			</div>
		{/if}
	</div>
</DetailSection>
