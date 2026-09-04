<script lang="ts">
	// One `.readout` cell in the ported generation panel's context rail
	// (last, elapsed, queue — the session/save cell is SessionCluster's own
	// `.session-control`, not this). Ported from generation-panel-concept.html's
	// `.readout` (lines 191-195, 452-454): fixed 69px width, label/value
	// stacked, border-right, mono value. A distinct component from the shared
	// ReadoutCell.svelte — that one is also used outside this ported scope
	// (admin GenerationStatTiles) and keeps its own Tailwind styling. A cell
	// never disappears — the caller always passes a value, falling back to
	// "none"/"empty" text upstream — so this component has no empty state.
	export let label: string;
	export let clickable = false;
	export let disabled = false;
	export let onclick: (() => void) | undefined = undefined;
	// Overrides the button's computed accessible name — needed when the
	// visible value text is state-dependent (a relative timestamp, a queue
	// count) and the trigger still needs one stable name for assistive tech
	// and tests to grab onto.
	export let ariaLabel: string | undefined = undefined;
	// A callback ref (not `bind:this`, which doesn't cross a component
	// boundary) so a clickable cell's caller can measure its rect — the
	// queue popover positions itself against this element, and it must stay
	// the mock's literal `.readout` (no extra wrapper div for the ref to
	// live on).
	export let onElement: ((el: HTMLElement) => void) | undefined = undefined;
	function bindElement(node: HTMLElement) {
		onElement?.(node);
	}
</script>

{#if clickable}
	<button
		type="button"
		class="readout"
		use:bindElement
		{disabled}
		aria-label={ariaLabel}
		on:click={() => {
			if (!disabled) onclick?.();
		}}
	>
		<span class="cell-label">{label}</span>
		<span class="cell-value"><slot /></span>
	</button>
{:else}
	<div class="readout" use:bindElement>
		<span class="cell-label">{label}</span>
		<span class="cell-value"><slot /></span>
	</div>
{/if}
