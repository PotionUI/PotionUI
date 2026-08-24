<script lang="ts">
	// The Stage half of the Stage & Rail Video Director rework (phase 2).
	// Standalone: not wired into VideoDirectorEditor yet. Subscribes to the
	// rail's selection store and renders whichever object it names, written
	// large -- a shot, a join, a keyframe, an audio clip, or the IC-LoRA head.
	// Direction/Negative stay quiet single-line rows underneath every
	// selection kind (applySetPrompt/applySetNegativePrompt) so a chat op and
	// a manual edit here produce the same document a phase-3 swap would.
	import type { VideoDirectorValue, DirectorCapabilities } from '$lib/types/videoDirector';
	import { deriveStageModel } from './stageModel';
	import { railSelection } from './railSelection';
	import { applySetPrompt, applySetNegativePrompt } from '$lib/utils/videoDirector';
	import StageEmpty from './StageEmpty.svelte';
	import StageShot from './StageShot.svelte';
	import StageJoin from './StageJoin.svelte';
	import StageKeyframe from './StageKeyframe.svelte';
	import StageAudio from './StageAudio.svelte';
	import StageIcLora from './StageIcLora.svelte';

	let {
		doc = $bindable(),
		caps,
		formData,
		presetId
	}: {
		doc: VideoDirectorValue;
		caps: DirectorCapabilities;
		formData: Record<string, unknown> | null | undefined;
		presetId: string;
	} = $props();

	let model = $derived(deriveStageModel(doc, caps, $railSelection, formData));

	// A director document holds the global prompt as a single-segment list
	// (see applySetPrompt) rather than plain text; read/write through the raw
	// segment content, never the flattened `global_prompt`, or the cursor
	// fights every trailing space -- see VideoDirectorEditor.svelte.
	let globalText = $state(doc.global_prompt_segments[0]?.content ?? doc.global_prompt);
	let negativeText = $state(doc.negative_prompt_segments[0]?.content ?? doc.negative_prompt);

	$effect(() => {
		const content = doc.global_prompt_segments[0]?.content ?? doc.global_prompt;
		if (content !== globalText) globalText = content;
	});
	$effect(() => {
		const content = doc.negative_prompt_segments[0]?.content ?? doc.negative_prompt;
		if (content !== negativeText) negativeText = content;
	});

	function setGlobalPrompt(text: string) {
		globalText = text;
		doc = applySetPrompt(doc, text);
	}
	function setNegativePrompt(text: string) {
		negativeText = text;
		doc = applySetNegativePrompt(doc, text);
	}
</script>

<div class="flex flex-col gap-3 rounded-lg border border-line bg-surface-1 p-4 shadow-floating">
	{#if model.selected.kind === 'shot'}
		<StageShot model={model.selected} {doc} {caps} {formData} {presetId} onDoc={(next) => (doc = next)} />
	{:else if model.selected.kind === 'seam'}
		<StageJoin model={model.selected} {doc} {caps} onDoc={(next) => (doc = next)} />
	{:else if model.selected.kind === 'keyframe'}
		<StageKeyframe model={model.selected} {doc} {caps} {formData} onDoc={(next) => (doc = next)} />
	{:else if model.selected.kind === 'audio'}
		<StageAudio model={model.selected} {doc} {caps} {formData} onDoc={(next) => (doc = next)} />
	{:else if model.selected.kind === 'ic_lora'}
		<StageIcLora model={model.selected} {doc} {formData} {presetId} onDoc={(next) => (doc = next)} />
	{:else}
		<StageEmpty />
	{/if}

	<div class="-mx-4 -mb-4 border-t border-surface-2">
		<div class="flex items-center border-b border-surface-2 transition-colors hover:bg-surface-2/40">
			<label for="stage-global-prompt" class="w-[84px] shrink-0 pl-[18px] font-mono text-2xs uppercase tracking-[0.08em] text-fg-subtle">Direction</label>
			<input
				id="stage-global-prompt"
				type="text"
				class="-ml-2 mr-4 min-w-0 flex-1 truncate rounded bg-transparent px-2 py-2 text-sm text-fg placeholder-fg-subtle focus:bg-surface-2 focus:outline-none focus:ring-1 focus:ring-line-strong"
				placeholder="Style, camera and grade that apply to every shot"
				value={globalText}
				oninput={(e) => setGlobalPrompt((e.currentTarget as HTMLInputElement).value)}
			/>
			<!-- Second label for the same input -- either it or the gutter label focuses the field; matches the mock's persistent low-key "Edit" affordance. -->
			<label for="stage-global-prompt" class="shrink-0 cursor-pointer pr-[18px] text-xs text-fg-subtle transition-colors hover:text-fg">Edit</label>
		</div>
		<div class="flex items-center transition-colors hover:bg-surface-2/40">
			<label for="stage-negative-prompt" class="w-[84px] shrink-0 pl-[18px] font-mono text-2xs uppercase tracking-[0.08em] text-danger">Negative</label>
			<input
				id="stage-negative-prompt"
				type="text"
				class="-ml-2 mr-4 min-w-0 flex-1 truncate rounded bg-transparent px-2 py-2 text-sm text-fg-muted placeholder-fg-subtle focus:bg-surface-2 focus:outline-none focus:ring-1 focus:ring-line-strong"
				placeholder="What to keep out"
				value={negativeText}
				oninput={(e) => setNegativePrompt((e.currentTarget as HTMLInputElement).value)}
			/>
			<label for="stage-negative-prompt" class="shrink-0 cursor-pointer pr-[18px] text-xs text-fg-subtle transition-colors hover:text-fg">Edit</label>
		</div>
	</div>
</div>
