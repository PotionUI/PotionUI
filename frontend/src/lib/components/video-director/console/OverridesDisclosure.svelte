<script lang="ts">
	// Steps / CFG per-shot overrides -- chain profiles only (a timeline
	// DirectorPromptSegment has no such fields at all, see PLAN.md §A). The
	// wire already accepts settings.steps/cfg; the editor's ChainSegment does
	// not carry them yet (W2), so both inputs render disabled with a title
	// explaining why rather than silently doing nothing on input.
	import Icon from '$lib/components/Icon.svelte';

	let collapsed = $state(true);
	const REASON = 'Per-shot overrides land in the next wave';
</script>

<div class="overrides" class:collapsed>
	<button type="button" class="overrides-head block-label" onclick={() => (collapsed = !collapsed)} aria-expanded={!collapsed}>
		Overrides
		<Icon name="chevron-down" className="icon" />
	</button>
	<div class="body2">
		<div class="overrides-body">
			<label class="overrides-field">
				<span class="fl">Steps</span>
				<input class="overrides-input tabular" placeholder="auto" disabled title={REASON} />
			</label>
			<label class="overrides-field">
				<span class="fl">CFG</span>
				<input class="overrides-input tabular" placeholder="auto" disabled title={REASON} />
			</label>
		</div>
	</div>
</div>

<style>
	.overrides-head {
		display: flex;
		align-items: center;
		gap: 8px;
		padding-bottom: 2px;
		font-family: 'IBM Plex Mono', monospace;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: rgb(var(--fg-subtle));
		background: none;
		border: none;
		cursor: pointer;
	}
	.overrides-head :global(.icon) {
		width: 12px;
		height: 12px;
		margin-left: auto;
		transition: transform 0.15s;
	}
	.overrides.collapsed .body2 {
		display: none;
	}
	.overrides.collapsed .overrides-head :global(.icon) {
		transform: rotate(-90deg);
	}
	.overrides-body {
		display: flex;
		gap: 16px;
		margin-top: 10px;
	}
	.overrides-field {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.overrides-field .fl {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 10px;
		text-transform: uppercase;
		color: rgb(var(--fg-subtle));
	}
	.overrides-input {
		width: 88px;
		height: 27px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong));
		background: rgb(var(--surface-2));
		color: rgb(var(--fg-disabled));
		font-size: 12px;
		font-family: 'IBM Plex Mono', monospace;
		padding: 0 9px;
	}
	.tabular {
		font-variant-numeric: tabular-nums;
	}
</style>
