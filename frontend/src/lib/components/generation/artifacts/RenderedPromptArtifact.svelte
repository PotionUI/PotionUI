<script lang="ts">
	import { alignTemplateToRendered, type PromptAlignment } from '$lib/utils/promptResolution';

	export let artifact: {
		artifact_data: {
			index: number;
			positive: string;
			negative: string;
		};
	};
	/** Set by GenerationPanelHistory to the number of sibling rendered_prompt
	 * artifacts in this output group; the per-image index is only meaningful
	 * (and only shown) once a generation actually produced more than one image. */
	export let totalImages: number | undefined = undefined;
	/** The pre-expansion prompt template ({a|b}/${var} intact) this image's
	 * prompt was rendered from - see GenerationState.submittedPromptTemplate.
	 * `null` when no template was captured (e.g. Video Director / Prompt Relay,
	 * which bypass dynamicprompts expansion entirely) - the card falls back to
	 * plain text in that case, same as when alignment can't be reconciled. */
	export let promptTemplate: { positive: string; negative: string } | null = null;

	$: positive = artifact.artifact_data.positive?.trim();
	$: negative = artifact.artifact_data.negative?.trim();
	$: showIndex = typeof totalImages === 'number' && totalImages > 1;

	// Independent per-occurrence re-rolls are unchanged - this only reconstructs,
	// after the fact, which substring of the ALREADY-EXPANDED text each dynamic
	// construct produced for THIS image. Never changes what ran, never invented
	// when alignment fails (alignTemplateToRendered returns null -> plain text).
	let positiveAlignment: PromptAlignment | null;
	$: positiveAlignment = promptTemplate && positive ? alignTemplateToRendered(promptTemplate.positive, positive) : null;
	let negativeAlignment: PromptAlignment | null;
	$: negativeAlignment = promptTemplate && negative ? alignTemplateToRendered(promptTemplate.negative, negative) : null;
</script>

{#snippet resolvedText(text: string, alignment: PromptAlignment | null)}
	{#if alignment}
		{#each alignment.spans as span}
			{#if span.type === 'resolved' && span.text}
				<mark
					class="rounded-sm bg-signal/10 text-signal px-0.5 no-underline"
					title={span.ambiguous ? `${span.label} (boundary ambiguous)` : span.label}>{span.text}</mark
				>
			{:else}
				{span.text}
			{/if}
		{/each}
	{:else}
		{text || '—'}
	{/if}
{/snippet}

{#snippet whatRolled(alignment: PromptAlignment | null)}
	{#if alignment && alignment.rolled.length > 0}
		<div class="space-y-0.5">
			{#each alignment.rolled as entry}
				<div class="flex flex-wrap items-baseline gap-x-1.5 text-2xs text-fg-subtle">
					<span
						class="break-words"
						title={entry.ambiguous ? 'Boundary ambiguous - merged with an adjacent dynamic construct' : undefined}
						>{entry.label}{entry.ambiguous ? ' *' : ''}</span
					>
					<span aria-hidden="true">→</span>
					<span class="font-mono text-fg-muted break-words">{entry.resolvedText || 'empty'}</span>
				</div>
			{/each}
		</div>
	{/if}
{/snippet}

<div class="space-y-2">
	<div class="flex items-center justify-between gap-2">
		<div class="flex items-center gap-2">
			<div class="p-1 bg-surface-3">
				<svg class="w-3.5 h-3.5 text-fg" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 8h10M7 12h10M7 16h6" />
				</svg>
			</div>
			<span class="text-xs font-semibold text-fg-muted">Rendered prompt</span>
		</div>
		{#if showIndex}
			<span class="font-mono tabular-nums text-xs text-fg-subtle">#{artifact.artifact_data.index}</span>
		{/if}
	</div>

	<div class="space-y-1">
		<p class="text-sm text-fg leading-relaxed whitespace-pre-wrap break-words">
			{@render resolvedText(positive, positiveAlignment)}
		</p>
		{@render whatRolled(positiveAlignment)}
	</div>

	{#if negative}
		<div class="space-y-1 border-l-2 border-line pl-2">
			<p class="text-xs text-fg-subtle leading-relaxed whitespace-pre-wrap break-words">
				{@render resolvedText(negative, negativeAlignment)}
			</p>
			{@render whatRolled(negativeAlignment)}
		</div>
	{/if}
</div>
