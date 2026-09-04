<script lang="ts">
	// `.film-row` from console.html: a gutter label, truncated text and a
	// segment-count chip when read-only; clicking the text swaps the row to
	// the full SegmentedPromptEditor so BREAK-style multi-segment global/
	// negative prompts (see UnifiedAIChat.svelte's `global_prompt_segments`
	// writer) are editable here, not just the flattened single-line text
	// `applySetPrompt`/`applySetNegativePrompt` produce (stage-rail/Stage.svelte
	// -- read-only reference for the doc-op idiom, not for this widget).
	//
	// `segments` is the raw `global_prompt_segments`/`negative_prompt_segments`
	// list -- the ConsoleFilmRow model only carries the flattened display text
	// and a segment count, not enough to drive a real editor, so this takes
	// the segments as a sibling prop rather than reading them off the model.
	import type { ConsoleFilmRow } from './consoleModel';
	import type { Segment } from '$lib/types/segments';
	import SegmentedPromptEditor from '$lib/components/SegmentedPromptEditor.svelte';
	import ConsoleIcon from './ConsoleIcon.svelte';

	let {
		row,
		position,
		segments,
		onSegmentsChange
	}: {
		row: ConsoleFilmRow;
		position: 'first' | 'last';
		segments: Segment[];
		onSegmentsChange: (segments: Segment[]) => void;
	} = $props();

	let editing = $state(false);

	function handleSegmentsChange(next: Segment[]) {
		onSegmentsChange(next);
	}
</script>

<div
	class="flex items-center border border-line bg-surface-1 {position === 'first'
		? 'rounded-t-md'
		: 'border-t-0 rounded-b-md'}"
	class:items-start={editing}
>
	<span
		class="w-24 flex-none self-stretch pl-3 pt-[9px] font-mono text-[10px] uppercase tracking-[0.06em] {row.kind === 'negative'
			? 'text-danger/85'
			: 'text-fg-subtle'}"
	>
		{row.label}
	</span>

	{#if !editing}
		<button
			type="button"
			class="min-w-0 flex-1 truncate px-2 py-[9px] text-left text-[12.5px] text-fg-muted hover:bg-surface-2/40"
			onclick={() => (editing = true)}
		>
			{row.text || (row.kind === 'negative' ? 'What to keep out' : 'Style, camera and grade that apply to every shot')}
		</button>
		{#if row.segmentCount != null}
			<span
				class="mr-2.5 inline-flex h-[22px] flex-none items-center gap-[5px] whitespace-nowrap rounded border border-line-strong bg-surface-2 px-2 font-mono text-[10px] uppercase tracking-[0.04em] text-fg-muted"
			>
				{row.segmentCount} segment{row.segmentCount === 1 ? '' : 's'}
			</span>
		{/if}
	{:else}
		<div class="min-w-0 flex-1 py-2 pl-2 pr-2">
			<SegmentedPromptEditor
				{segments}
				isNegative={row.kind === 'negative'}
				label={row.label}
				showPreview={false}
				compact
				showLibraryActions={false}
				on:segmentsChange={(e) => handleSegmentsChange(e.detail)}
			/>
		</div>
		<button
			type="button"
			class="mr-2.5 mt-[9px] inline-flex h-5 w-5 flex-none items-center justify-center rounded text-fg-subtle hover:bg-surface-2 hover:text-fg"
			aria-label="Done editing {row.label.toLowerCase()}"
			onclick={() => (editing = false)}
		>
			<ConsoleIcon name="check" class="h-2.5 w-2.5" />
		</button>
	{/if}
</div>
