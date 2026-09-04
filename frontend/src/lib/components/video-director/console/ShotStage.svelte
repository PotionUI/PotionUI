<script lang="ts">
	// The stage half of one shot card: a tab row (only when the shot has tabs
	// beyond Selection -- TabsField.svelte:268's idiom, held to console.html's
	// own `.stage-tabs`/`.stage-tab` geometry rather than that field's Tailwind
	// classes per the console brief) plus whichever variant the current rail
	// selection (or lack of one) calls for. Reuses the EXISTING
	// deriveStageModel/StageShotModel derivation from stage-rail/stageModel.ts
	// unchanged -- a "shot" selection there already models exactly what the
	// console calls a prompt beat (PLAN.md §B); this file only re-skins the
	// selection into the console's own tab/variant shell.
	import type { VideoDirectorValue, DirectorCapabilities } from '$lib/types/videoDirector';
	import type { ConsoleShot } from './consoleModel';
	import type { ConsoleSelection } from './consoleSelection';
	import { deriveStageModel } from '../stage-rail/stageModel';
	import type { RailSelectionId } from '../stage-rail/railModel';
	import StageBeat from './StageBeat.svelte';
	import StageGlobalFallback from './StageGlobalFallback.svelte';
	import StageLorasTab from './StageLorasTab.svelte';
	import StageReferencesTab from './StageReferencesTab.svelte';
	import StageKeyframe from '../stage-rail/StageKeyframe.svelte';
	import StageAudio from '../stage-rail/StageAudio.svelte';
	import StageIcLora from '../stage-rail/StageIcLora.svelte';

	let {
		shot,
		doc,
		caps,
		formData,
		presetId,
		selection,
		onDoc
	}: {
		shot: ConsoleShot;
		doc: VideoDirectorValue;
		caps: DirectorCapabilities;
		formData: Record<string, unknown> | null | undefined;
		presetId: string;
		selection: ConsoleSelection;
		onDoc: (next: VideoDirectorValue) => void;
	} = $props();

	let activeTab: ConsoleShot['tabs'][number]['id'] = $state('selection');

	// Whatever's picked on the rail is the tab's actual default (console.html's
	// Wan/LTX frames) -- clicking a keyframe/beat/audio object always swaps
	// back to Selection, independent of whichever tab the user last clicked.
	let selectionKey = $derived(selection && selection.shotId === shot.id ? `${selection.kind}:${selection.id}` : null);
	let lastSelectionKey: string | null = null;
	$effect(() => {
		if (selectionKey !== null && selectionKey !== lastSelectionKey) {
			activeTab = 'selection';
		}
		lastSelectionKey = selectionKey;
	});

	let railSelection: RailSelectionId | null = $derived(
		selection && selection.shotId === shot.id ? { kind: selection.kind === 'beat' ? 'shot' : selection.kind, id: selection.id } : null
	);
	let stageModel = $derived(deriveStageModel(doc, caps, railSelection, formData));

	function formatSeconds(n: number): string {
		return n.toFixed(1);
	}

	let capText = $derived.by(() => {
		const sel = stageModel.selected;
		if (sel.kind === 'shot') {
			if (sel.footer.startSeconds != null && sel.footer.endSeconds != null) {
				return `Prompt beat — ${formatSeconds(sel.footer.startSeconds)} – ${formatSeconds(sel.footer.endSeconds)} s`;
			}
			return 'Prompt beat';
		}
		if (sel.kind === 'keyframe') {
			const roleLabel = sel.role === 'first' ? 'start' : sel.role === 'last' ? 'end' : sel.role === 'keyframe' ? 'anywhere' : 'free';
			return `Keyframe — ${roleLabel}, ${formatSeconds(sel.atSeconds)} s`;
		}
		if (sel.kind === 'audio') {
			const roleLabel = sel.role === 'mux' ? 'mux' : 'condition';
			return `Audio — ${roleLabel}, ${formatSeconds(sel.startSeconds)}–${formatSeconds(sel.startSeconds + sel.lengthSeconds)} s`;
		}
		return '';
	});

	/** Tab labels arrive pre-formatted ('LoRAs · 2', 'References · 2 of 5') --
	 * split back into base label + count chip to match console.html's
	 * `<button>LoRAs<span class="cnt2">2</span></button>` anatomy. */
	function splitLabel(label: string): [string, string | null] {
		const idx = label.indexOf(' · ');
		return idx === -1 ? [label, null] : [label.slice(0, idx), label.slice(idx + 3)];
	}
</script>

<div class="stage">
	{#if shot.tabs.length > 1}
		<div class="stage-tabs" role="tablist" aria-label="{shot.title} stage tabs">
			{#each shot.tabs as tab (tab.id)}
				{@const [base, count] = splitLabel(tab.label)}
				<button
					type="button"
					class="stage-tab"
					class:active={activeTab === tab.id}
					role="tab"
					aria-selected={activeTab === tab.id}
					onclick={() => (activeTab = tab.id)}
				>
					{base}
					{#if count != null}<span class="cnt2">{count}</span>{/if}
				</button>
			{/each}
		</div>
	{/if}

	{#if activeTab === 'selection'}
		{#if capText}<div class="stage-cap">{capText}</div>{/if}
		{#if stageModel.selected.kind === 'shot'}
			<StageBeat model={stageModel.selected} {doc} {caps} {onDoc} />
		{:else if stageModel.selected.kind === 'keyframe'}
			<StageKeyframe model={stageModel.selected} {doc} {caps} {formData} {onDoc} />
		{:else if stageModel.selected.kind === 'audio'}
			<StageAudio model={stageModel.selected} {doc} {caps} {formData} {onDoc} />
		{:else}
			<StageGlobalFallback globalPromptText={stageModel.globalPrompt} />
		{/if}
	{:else if activeTab === 'loras'}
		<StageLorasTab {doc} {caps} shotId={shot.id} {presetId} {onDoc} />
	{:else if activeTab === 'references'}
		<StageReferencesTab {doc} {caps} {formData} shotId={shot.id} {onDoc} />
	{:else if activeTab === 'ic_lora'}
		<StageIcLora {doc} {formData} {presetId} {onDoc} />
	{/if}
</div>

<style>
	.stage {
		border-top: 1px solid rgb(var(--line));
		padding-top: 14px;
	}
	.stage-tabs {
		display: flex;
		align-items: center;
		gap: 4px;
		border-bottom: 1px solid rgb(var(--line));
		margin-bottom: 12px;
	}
	.stage-tab {
		padding: 7px 9px;
		font-size: 12px;
		font-weight: 500;
		color: rgb(var(--fg-muted));
		background: none;
		border: none;
		border-bottom: 2px solid transparent;
		margin-bottom: -1px;
		cursor: pointer;
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.stage-tab.active {
		color: rgb(var(--signal));
		border-bottom-color: rgb(var(--signal));
	}
	.stage-tab:not(.active):hover {
		color: rgb(var(--fg));
		border-bottom-color: rgb(var(--line-hover));
	}
	.cnt2 {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 9px;
		color: rgb(var(--fg-subtle));
		background: rgb(var(--surface-2));
		border: 1px solid rgb(var(--line-strong));
		border-radius: 4px;
		padding: 0 5px;
		height: 15px;
		display: inline-flex;
		align-items: center;
	}
	.stage-cap {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: rgb(var(--fg-subtle));
		margin-bottom: 10px;
	}
</style>
