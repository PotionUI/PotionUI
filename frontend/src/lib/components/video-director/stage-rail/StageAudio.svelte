<script lang="ts">
	import type { VideoDirectorValue, DirectorCapabilities, DirectorMediaValue } from '$lib/types/videoDirector';
	import type { StageAudioModel } from './stageModel';
	import { withAudioPatch, withRemoveAudio } from './stageModel';
	import DirectorMediaSlot from '../DirectorMediaSlot.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { IconButton } from '$lib/components/ui';
	import StageCard from './StageCard.svelte';
	import StageField from './StageField.svelte';

	let {
		model,
		doc,
		caps,
		timelineShotId,
		formData,
		onDoc
	}: {
		model: StageAudioModel;
		doc: VideoDirectorValue;
		caps: DirectorCapabilities;
		timelineShotId: string;
		formData: Record<string, unknown> | null | undefined;
		onDoc: (next: VideoDirectorValue) => void;
	} = $props();

	function setMedia(value: DirectorMediaValue | null) {
		if (value == null) {
			onDoc(withRemoveAudio(doc, caps, timelineShotId, model.id));
			return;
		}
		onDoc(withAudioPatch(doc, caps, timelineShotId, model.id, { media: value }));
	}
	function setRole(role: 'mux' | 'condition') {
		onDoc(withAudioPatch(doc, caps, timelineShotId, model.id, { role }));
	}
	function remove() {
		onDoc(withRemoveAudio(doc, caps, timelineShotId, model.id));
	}
	function readNumber(e: Event, fallback: number): number {
		const parsed = parseFloat((e.currentTarget as HTMLInputElement).value);
		return Number.isFinite(parsed) ? parsed : fallback;
	}
	function setStart(e: Event) {
		onDoc(withAudioPatch(doc, caps, timelineShotId, model.id, { start: Math.max(0, readNumber(e, model.startSeconds)) }));
	}
	function setTrimStart(e: Event) {
		onDoc(withAudioPatch(doc, caps, timelineShotId, model.id, { trim_start: Math.max(0, readNumber(e, model.trimStartSeconds)) }));
	}
	function setLength(e: Event) {
		onDoc(withAudioPatch(doc, caps, timelineShotId, model.id, { length: Math.max(0.1, readNumber(e, model.lengthSeconds)) }));
	}
</script>

<StageCard>
	{#snippet title()}
		<span class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle">Audio</span>
	{/snippet}
	{#snippet actions()}
		<Tooltip text="Remove audio" position="top">
			<IconButton icon="trash" label="Remove audio" size="sm" onclick={remove} />
		</Tooltip>
	{/snippet}

	<div class="flex flex-col gap-3.5">
		<div class="flex items-start gap-5">
			<div class="w-[190px] flex-shrink-0">
				<DirectorMediaSlot name="{model.id}-media" value={model.media} {formData} kind="audio" onChange={setMedia} config={{ accept: 'audio/*' }} />
			</div>
			<div class="min-w-0 flex-1 text-sm leading-6 text-fg">
				{#if model.role === 'mux'}
					Laid over the finished video from <span class="font-mono">{model.startSeconds.toFixed(2)} s</span>, {model.lengthSeconds.toFixed(2)} s long. It
					does not change what is generated.
				{:else}
					Fed into generation from <span class="font-mono">{model.startSeconds.toFixed(2)} s</span>, {model.lengthSeconds.toFixed(2)} s long.
				{/if}
			</div>
		</div>

		<div class="flex items-center gap-2.5">
			<span class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Role</span>
			<div class="flex gap-0.5 rounded-md bg-surface-1 p-0.5 ring-1 ring-inset ring-line">
				<button
					type="button"
					class="rounded px-2.5 py-1 text-xs font-medium {model.role === 'mux' ? 'bg-surface-3 text-fg' : 'text-fg-muted hover:text-fg'}"
					onclick={() => setRole('mux')}
				>
					Mux
				</button>
				<button
					type="button"
					class="rounded px-2.5 py-1 text-xs font-medium {model.role === 'condition' ? 'bg-surface-3 text-fg' : 'text-fg-muted hover:text-fg'}"
					onclick={() => setRole('condition')}
				>
					Condition
				</button>
			</div>
			{#if model.showConditionWarning}
				<span class="text-xs text-warning">Condition is accepted, but this generator may refuse it and mux instead.</span>
			{/if}
		</div>

		<div class="flex flex-wrap items-center gap-4">
			<StageField label="Start (s)">
				<input type="number" min="0" step="0.1" class="input w-16 py-1 text-xs tabular-nums" value={model.startSeconds} oninput={setStart} />
			</StageField>
			<StageField label="Trim start (s)">
				<input type="number" min="0" step="0.1" class="input w-16 py-1 text-xs tabular-nums" value={model.trimStartSeconds} oninput={setTrimStart} />
			</StageField>
			<StageField label="Length (s)">
				<input type="number" min="0.1" step="0.1" class="input w-16 py-1 text-xs tabular-nums" value={model.lengthSeconds} oninput={setLength} />
			</StageField>
		</div>
	</div>
</StageCard>
