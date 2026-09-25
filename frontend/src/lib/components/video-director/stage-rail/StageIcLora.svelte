<script lang="ts">
	import type { VideoDirectorValue, DirectorLoraRef, DirectorMediaValue } from '$lib/types/videoDirector';
	import type { LoraPickerItem } from '$lib/types/models';
	import { withIcLoraPatch, withRemoveIcLora } from './stageModel';
	import { mintId } from '../timelineCore';
	import DirectorMediaSlot from '../DirectorMediaSlot.svelte';
	import LoraPickerField from '$lib/components/form-fields/LoraPickerField.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { IconButton } from '$lib/components/ui';
	import StageCard from './StageCard.svelte';
	import StageField from './StageField.svelte';

	let {
		doc,
		timelineShotId,
		formData,
		presetId,
		onDoc
	}: {
		doc: VideoDirectorValue;
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

<div class="icl-tab flex flex-col gap-3">
	{#if entries.length === 0}
		<p class="text-xs text-fg-subtle">
			Drives generation from a reference image through a dedicated LoRA. Add one to get started.
		</p>
	{:else}
		<div class="flex flex-col gap-3">
			{#each entries as entry, index (entry.id)}
				<StageCard class="icl-card">
					{#snippet title()}
						<span class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle">
							IC-LoRA <span class="tabular-nums">{index + 1}</span>
						</span>
					{/snippet}
					{#snippet actions()}
						<Tooltip text="Remove IC-LoRA" position="top">
							<IconButton icon="close" label="Remove IC-LoRA" size="sm" onclick={() => removeEntry(entry.id)} />
						</Tooltip>
					{/snippet}
					{#snippet media()}
						<DirectorMediaSlot
							name="{entry.id}-reference"
							value={entry.ref_media}
							{formData}
							kind="image"
							fill
							onChange={(v) => setReference(entry.id, v)}
							config={{ accept: 'image/*' }}
						/>
					{/snippet}

					<LoraPickerField
						name="{entry.id}-lora"
						value={entry.lora ? [entry.lora] : []}
						onChange={(_n, v) => setLora(entry.id, v as LoraPickerItem[])}
						config={{ title: 'IC-LoRA', preset_id: presetId, configuration: { model_type: 'lora', max_items: 1 } }}
						compact
					/>

					<StageField label="Reference strength">
						<div class="flex items-center gap-2">
							<input
								type="range"
								min="0"
								max="1"
								step="0.01"
								class="strength-slider h-2 flex-1 cursor-pointer appearance-none rounded-lg bg-surface-3 accent-signal"
								value={entry.strength}
								oninput={(e) => setStrength(entry.id, parseFloat((e.currentTarget as HTMLInputElement).value))}
							/>
							<span class="w-12 shrink-0 text-right font-mono text-xs tabular-nums text-fg-muted">
								{entry.strength.toFixed(2)}
							</span>
						</div>
					</StageField>
				</StageCard>
			{/each}
		</div>
	{/if}

	<button type="button" class="add-icl" onclick={addEntry}>
		<Icon name="plus" className="w-3 h-3" />
		<span>Add IC-LoRA</span>
	</button>
</div>

<style>
	.add-icl {
		width: 100%;
		height: 40px;
		margin: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 6px;
		color: rgb(var(--fg-muted));
		background: transparent;
		border: 1px dashed rgb(var(--line-strong));
		border-radius: 6px;
		font-size: 12px;
		cursor: pointer;
	}

	.add-icl:hover {
		color: rgb(var(--fg));
		background: rgb(var(--surface-1));
		border-color: rgb(var(--line-hover));
	}
</style>
