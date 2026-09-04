<script lang="ts">
	// LoRAs stage tab -- chain profiles with `perSegmentLoras` only (a shot's
	// tabs never include 'loras' otherwise). Lifted from
	// StageShot.svelte:372-386's high/low LoraPickerField pair, unconditional
	// here since the tab's very presence already gates on the capability.
	import type { VideoDirectorValue, DirectorCapabilities } from '$lib/types/videoDirector';
	import type { LoraPickerItem } from '$lib/types/models';
	import { withShotLoras } from '../stage-rail/stageModel';
	import LoraPickerField from '$lib/components/form-fields/LoraPickerField.svelte';

	let {
		doc,
		caps,
		shotId,
		presetId,
		onDoc
	}: {
		doc: VideoDirectorValue;
		caps: DirectorCapabilities;
		shotId: string;
		presetId: string;
		onDoc: (next: VideoDirectorValue) => void;
	} = $props();

	let segment = $derived(doc.chain.segments.find((s) => s.id === shotId));
	let stacks = $derived(segment?.loras ?? { high: [], low: [] });

	function patchLoras(partial: Partial<{ high: LoraPickerItem[]; low: LoraPickerItem[] }>) {
		onDoc(withShotLoras(doc, caps, shotId, { ...stacks, ...partial }));
	}
</script>

<div class="lora-tab">
	<LoraPickerField
		name="{shotId}-high"
		value={stacks.high}
		onChange={(_n, v) => patchLoras({ high: v as LoraPickerItem[] })}
		config={{ preset_id: presetId, title: 'HIGH-NOISE', configuration: { model_type: 'lora' } }}
	/>
	<LoraPickerField
		name="{shotId}-low"
		value={stacks.low}
		onChange={(_n, v) => patchLoras({ low: v as LoraPickerItem[] })}
		config={{ preset_id: presetId, title: 'LOW-NOISE', configuration: { model_type: 'lora' } }}
	/>
</div>

<style>
	.lora-tab {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
</style>
