<script lang="ts">
	// Steps / CFG per-shot overrides -- chain profiles only (a timeline
	// DirectorPromptSegment has no such fields at all, see PLAN.md §A).
	// `ChainSegment.steps`/`cfg` (W2) are the live storage; an empty input
	// clears back to `null` ("auto" -- let the backend use its own default),
	// same as every other numeric override field in this feature.
	import type { VideoDirectorValue, DirectorCapabilities } from '$lib/types/videoDirector';
	import { applyDirectorOperations } from '$lib/utils/videoDirector';
	import Icon from '$lib/components/Icon.svelte';

	let {
		doc,
		caps,
		shotId,
		onDoc
	}: {
		doc: VideoDirectorValue;
		caps: DirectorCapabilities;
		shotId: string;
		onDoc: (next: VideoDirectorValue) => void;
	} = $props();

	let collapsed = $state(true);
	let segment = $derived(doc.chain.segments.find((s) => s.id === shotId));

	function readNumberOrNull(e: Event): number | null {
		const raw = (e.currentTarget as HTMLInputElement).value.trim();
		if (raw === '') return null;
		const parsed = parseFloat(raw);
		return Number.isFinite(parsed) ? parsed : null;
	}
	function setSteps(e: Event) {
		onDoc(applyDirectorOperations(doc, [{ op: 'upsert_segment', segment: { id: shotId, steps: readNumberOrNull(e) } }], caps));
	}
	function setCfg(e: Event) {
		onDoc(applyDirectorOperations(doc, [{ op: 'upsert_segment', segment: { id: shotId, cfg: readNumberOrNull(e) } }], caps));
	}
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
				<input
					class="overrides-input tabular"
					type="number"
					min="1"
					step="1"
					placeholder="auto"
					value={segment?.steps ?? ''}
					onchange={setSteps}
				/>
			</label>
			<label class="overrides-field">
				<span class="fl">CFG</span>
				<input class="overrides-input tabular" type="number" min="0" step="0.1" placeholder="auto" value={segment?.cfg ?? ''} onchange={setCfg} />
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
		color: rgb(var(--fg));
		font-size: 12px;
		font-family: 'IBM Plex Mono', monospace;
		padding: 0 9px;
	}
	.tabular {
		font-variant-numeric: tabular-nums;
	}
</style>
