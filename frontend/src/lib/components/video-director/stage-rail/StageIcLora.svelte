<script lang="ts">
	// IC-LoRA stage tab: console.html moves IC-LoRA off the rail entirely
	// (PLAN.md §A: "a stage tab on LTX shots, not a film-level row") and
	// renders the whole `ic_lora[]` list as `.icl-item` rows -- reference
	// well, LoRA picker, strength, remove -- plus "Add IC-LoRA". This
	// replaces the old single-entry, rail-selection-driven StageIcLoraModel
	// render (StageIcLoraModel/buildIcLoraModel/RailIcLoraHead go unused by
	// the console -- selection no longer includes an 'ic_lora' kind; only
	// consoleSelection.ts's keyframe/beat/audio do) with a list editor over
	// the ACTIVE shot's own `ic_lora` (W2: per-shot, not document-level).
	// `withIcLoraPatch` already
	// upserts-by-id (mintId + a not-yet-present id inserts), so "Add" needs
	// no new op builder; `withRemoveIcLora` (stageModel.ts) is the one
	// addition, filtering the list the way no existing op needed to.
	//
	// Deviation: each row's reference well renders the existing
	// DirectorMediaSlot widget (fill mode) rather than the mock's flat
	// 96x54 background-image box -- same rationale as StageKeyframe (keeps
	// upload/library/"from form" working); MediaLoaderField's fill layout
	// enforces a larger minimum height than 54px, so rows run taller than
	// the mock. The LoRA picker is the existing full LoraPickerField widget
	// (search, triggers, its own strength control) rather than the mock's
	// compact chip -- a dedicated compact single-LoRA chip is future work.
	import type { VideoDirectorValue, DirectorLoraRef, DirectorMediaValue } from '$lib/types/videoDirector';
	import type { LoraPickerItem } from '$lib/types/models';
	import { withIcLoraPatch, withRemoveIcLora } from './stageModel';
	import { mintId } from '../timelineCore';
	import DirectorMediaSlot from '../DirectorMediaSlot.svelte';
	import LoraPickerField from '$lib/components/form-fields/LoraPickerField.svelte';

	let {
		doc,
		timelineShotId,
		formData,
		presetId,
		onDoc
	}: {
		doc: VideoDirectorValue;
		/** Which shot's own `ic_lora` list this tab reads/writes (IC-LoRA is
		 * per-shot, PLAN.md §B/W2's "13:20 ruling"). */
		timelineShotId: string;
		formData: Record<string, unknown> | null | undefined;
		presetId: string;
		onDoc: (next: VideoDirectorValue) => void;
	} = $props();

	let entries = $derived(doc.timeline.shots.find((s) => s.id === timelineShotId)?.ic_lora ?? []);

	function setReference(id: string, value: DirectorMediaValue | null) {
		onDoc(withIcLoraPatch(doc, timelineShotId, id, { ref_media: value }));
	}
	function setLora(id: string, items: LoraPickerItem[]) {
		onDoc(withIcLoraPatch(doc, timelineShotId, id, { lora: (items[0] as DirectorLoraRef | undefined) ?? null }));
	}
	function setStrength(id: string, strength: number) {
		onDoc(withIcLoraPatch(doc, timelineShotId, id, { strength }));
	}
	function removeEntry(id: string) {
		onDoc(withRemoveIcLora(doc, timelineShotId, id));
	}
	function addEntry() {
		onDoc(withIcLoraPatch(doc, timelineShotId, mintId('ic-lora', entries), {}));
	}
</script>

<div class="icl-tab">
	{#if entries.length > 0}
		<div class="icl-list">
			{#each entries as entry (entry.id)}
				<div class="icl-item">
					<div class="icl-well">
						<DirectorMediaSlot
							name="{entry.id}-reference"
							value={entry.ref_media}
							{formData}
							kind="image"
							fill
							onChange={(v) => setReference(entry.id, v)}
							config={{ accept: 'image/*' }}
						/>
					</div>
					<div class="icl-lora">
						<LoraPickerField
							name="{entry.id}-lora"
							value={entry.lora ? [entry.lora] : []}
							onChange={(_n, v) => setLora(entry.id, v as LoraPickerItem[])}
							config={{ preset_id: presetId, title: 'IC-LoRA', configuration: { model_type: 'lora' } }}
						/>
					</div>
					<div class="icl-strength">
						<span class="fl">Strength</span>
						<input
							type="range"
							min="0"
							max="1"
							step="0.01"
							class="strength-slider"
							value={entry.strength}
							oninput={(e) => setStrength(entry.id, parseFloat((e.currentTarget as HTMLInputElement).value))}
						/>
						<span class="mono tabular">{entry.strength.toFixed(2)}</span>
					</div>
					<button type="button" class="icon-btn sm" onclick={() => removeEntry(entry.id)} aria-label="Remove IC-LoRA">
						<svg class="icon" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M6 18L18 6M6 6l12 12" /></svg>
					</button>
				</div>
			{/each}
		</div>
	{/if}
	<button type="button" class="btn icl-add" onclick={addEntry}>
		<svg class="icon" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M12 4v16m8-8H4" /></svg>
		Add IC-LoRA
	</button>
</div>

<style>
	.icl-tab {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.icl-list {
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.icl-item {
		display: flex;
		align-items: flex-start;
		gap: 12px;
	}
	.icl-well {
		width: 96px;
		min-height: 54px;
		border-radius: 4px;
		flex: none;
		border: 1px solid rgb(var(--line-strong));
		overflow: hidden;
	}
	.icl-lora {
		flex: 1;
		min-width: 0;
	}
	.icl-strength {
		display: flex;
		align-items: center;
		gap: 8px;
		flex: none;
		padding-top: 6px;
	}
	.icl-strength .fl {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: rgb(var(--fg-subtle));
	}
	.strength-slider {
		width: 100px;
		accent-color: rgb(var(--signal));
	}
	.mono {
		font-family: 'IBM Plex Mono', monospace;
	}
	.tabular {
		font-variant-numeric: tabular-nums;
	}
	.icon-btn.sm {
		width: 20px;
		height: 20px;
		border-radius: 4px;
		border: 1px solid transparent;
		background: transparent;
		color: rgb(var(--fg-subtle));
		display: inline-flex;
		align-items: center;
		justify-content: center;
		cursor: pointer;
		flex: none;
		margin-top: 6px;
	}
	.icon-btn.sm:hover {
		background: rgb(var(--surface-2));
		color: rgb(var(--fg));
	}
	.icon-btn.sm .icon {
		width: 11px;
		height: 11px;
	}
	.icl-add {
		align-self: flex-start;
		margin-top: 2px;
		height: 27px;
		padding: 0 10px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong));
		background: rgb(var(--surface-2));
		color: rgb(var(--fg));
		font-size: 12px;
		display: inline-flex;
		align-items: center;
		gap: 6px;
		cursor: pointer;
	}
	.icl-add:hover {
		background: rgb(var(--surface-3));
	}
	.icon {
		width: 12px;
		height: 12px;
		stroke: currentColor;
		fill: none;
		flex: none;
	}
</style>
