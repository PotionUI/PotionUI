<script lang="ts">
	// The IC-LoRA head, staged: a whole-video asset with no time anchor --
	// framed explicitly as applying to every frame rather than floating below
	// everything with unclear scope.
	import type { DirectorLoraRef, DirectorMediaValue } from '$lib/types/videoDirector';
	import type { LoraPickerItem } from '$lib/types/models';
	import type { StageIcLoraModel } from './stageModel';
	import { withIcLoraPatch } from './stageModel';
	import type { VideoDirectorValue } from '$lib/types/videoDirector';
	import DirectorMediaSlot from '../DirectorMediaSlot.svelte';
	import LoraPickerField from '$lib/components/form-fields/LoraPickerField.svelte';

	let {
		model,
		doc,
		formData,
		presetId,
		onDoc
	}: {
		model: StageIcLoraModel;
		doc: VideoDirectorValue;
		formData: Record<string, unknown> | null | undefined;
		presetId: string;
		onDoc: (next: VideoDirectorValue) => void;
	} = $props();

	// LoraPickerItem and DirectorLoraRef share the same wire shape
	// ({ model, strength, saved_strength? }) -- no translation needed.
	let loraValue: LoraPickerItem[] = $derived(model.lora ? [model.lora] : []);

	function setLora(items: LoraPickerItem[]) {
		onDoc(withIcLoraPatch(doc, model.id, { lora: (items[0] as DirectorLoraRef | undefined) ?? null }));
	}
	function setReference(value: DirectorMediaValue | null) {
		onDoc(withIcLoraPatch(doc, model.id, { ref_media: value }));
	}
	function setStrength(strength: number) {
		onDoc(withIcLoraPatch(doc, model.id, { strength }));
	}
</script>

<div class="flex flex-col gap-3.5">
	<div class="flex items-center gap-2.5">
		<span class="rounded bg-signal px-1.5 py-0.5 font-mono text-2xs font-semibold uppercase tracking-wide text-canvas">IC-LoRA</span>
		<span class="text-sm font-semibold text-fg">Whole-video reference</span>
		<div class="flex-1"></div>
		<span class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">applies to every frame</span>
	</div>

	<div class="flex items-start gap-5">
		<div class="w-[190px] flex-shrink-0">
			<DirectorMediaSlot name="{model.id}-reference" value={model.refMedia} {formData} kind="image" onChange={setReference} config={{ accept: 'image/*' }} />
			<div class="mt-1.5 text-2xs text-fg-subtle">Reference image</div>
		</div>
		<div class="min-w-0 flex-1">
			<LoraPickerField
				name="{model.id}-lora"
				value={loraValue}
				onChange={(_n, v) => setLora(v as LoraPickerItem[])}
				config={{ preset_id: presetId, title: 'IC-LoRA', configuration: { model_type: 'lora' } }}
			/>
			<label class="mt-3 flex items-center gap-2 text-xs">
				<span class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Strength</span>
				<input
					type="range"
					min="0"
					max="1"
					step="0.01"
					class="w-32 accent-signal"
					value={model.strength}
					oninput={(e) => setStrength(parseFloat((e.currentTarget as HTMLInputElement).value))}
				/>
				<span class="font-mono tabular-nums text-fg-muted">{model.strength.toFixed(2)}</span>
			</label>
		</div>
	</div>
</div>
