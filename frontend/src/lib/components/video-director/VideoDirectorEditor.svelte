<script lang="ts">
	// Top-level Video Director editor: modeless -- there is no mode switch.
	// A capability-shaped Stage (the selected object, written large) and Rail
	// (the whole sequence: shots, keyframes, audio, joins) are the entire
	// surface; `mode` on the document is a derived read of their structure
	// (deriveDirectorMode), kept coherent on every edit so the wire contract
	// and chat tooling (which still key off it) see the truth.
	import { untrack } from 'svelte';
	import type { VideoDirectorValue, DirectorCapabilities } from '$lib/types/videoDirector';
	import { normalizeDirectorValue, toModelessDirectorValue, deriveDirectorMode } from '$lib/utils/videoDirector';
	import { deriveRailModel } from './stage-rail/railModel';
	import { railSelection, selectRailObject, clearRailSelection } from './stage-rail/railSelection';
	import Stage from './stage-rail/Stage.svelte';
	import Rail from './stage-rail/Rail.svelte';
	import Icon from '$lib/components/Icon.svelte';

	let { value, capabilities, presetId, formData, onChange, onOpenVariables, variableCount = 0 }: {
		value: VideoDirectorValue | undefined;
		capabilities: DirectorCapabilities;
		presetId: string;
		/** The generate form's own field values -- threaded down so a Director
		 * media slot can offer "From form" (Stage B reference media). */
		formData: Record<string, unknown> | null | undefined;
		onChange: (v: VideoDirectorValue) => void;
		// Video Director renders its own header (unlike the prompt-relay/
		// segmented-prompt editors), so the Variables entry point that used to
		// float in a standalone row above it lives here instead. State and the
		// modal itself stay owned by the caller (PromptSection.svelte).
		onOpenVariables?: () => void;
		variableCount?: number;
	} = $props();

	function project(raw: unknown): VideoDirectorValue {
		return toModelessDirectorValue(normalizeDirectorValue(raw, capabilities), capabilities);
	}

	let doc = $state(project(value));

	// `lastEmitted` tracks the last document we either adopted from `value` or
	// handed to `onChange` -- a plain (non-reactive) closure variable, not
	// `$state`, so comparing against it never itself triggers a re-run. It's
	// what lets the two effects below tell "the external `value` prop changed
	// because OUR OWN last emit round-tripped through it" apart from "a real
	// external change (chat tool, tab switch) landed" without an explicit
	// emit() chokepoint -- Stage/Rail write `doc` directly via `bind:doc`, not
	// through a callback this component controls.
	let lastEmitted: VideoDirectorValue = untrack(() => doc);

	$effect(() => {
		if (lastEmitted && JSON.stringify(value) === JSON.stringify(lastEmitted)) return;
		const next = project(value);
		doc = next;
		lastEmitted = next;
	});

	$effect(() => {
		const derivedMode = deriveDirectorMode(doc, capabilities);
		if (doc.mode !== derivedMode) {
			doc = { ...doc, mode: derivedMode };
			return;
		}
		if (JSON.stringify(doc) === JSON.stringify(lastEmitted)) return;
		lastEmitted = doc;
		onChange(doc);
	});

	// Selection has no natural default (railSelection starts null) -- keep it
	// resolved to the nearest shot whenever nothing is selected, or the
	// selection dangles (its object was removed). Runs after every doc change.
	$effect(() => {
		const model = deriveRailModel(doc, capabilities);
		const sel = $railSelection;
		const resolves =
			sel != null &&
			((sel.kind === 'shot' && model.shots.some((s) => s.id === sel.id)) ||
				(sel.kind === 'seam' && model.seams.some((s) => s.id === sel.id)) ||
				(sel.kind === 'keyframe' && model.keyframes.some((k) => k.id === sel.id)) ||
				(sel.kind === 'audio' && model.audio.some((a) => a.id === sel.id)) ||
				(sel.kind === 'ic_lora' && model.icLora?.id === sel.id));
		if (resolves) return;
		if (model.shots.length > 0) selectRailObject('shot', model.shots[0].id);
		else clearRailSelection();
	});
</script>

<section class="video-director space-y-4" aria-label="Video Director">
	<header class="flex flex-wrap items-center gap-3 border-b border-line pb-3">
		<h2 class="text-lg font-semibold leading-tight text-fg">Video Director</h2>
		{#if onOpenVariables}
			<div class="ml-auto flex max-w-full flex-wrap items-center gap-2">
				<button
					type="button"
					class="inline-flex h-8 items-center gap-1.5 rounded border border-line px-2.5 text-xs font-medium text-fg-muted transition-colors hover:border-line-hover hover:bg-surface-2 hover:text-fg"
					onclick={onOpenVariables}
				>
					<Icon name="braces" className="h-3.5 w-3.5" />
					<span>Variables</span>
					{#if variableCount > 0}
						<span class="rounded bg-signal/15 px-1.5 py-0.5 font-mono text-2xs tabular-nums text-signal">{variableCount}</span>
					{/if}
				</button>
			</div>
		{/if}
	</header>

	<Stage bind:doc caps={capabilities} {formData} {presetId} />
	<Rail bind:doc caps={capabilities} {formData} />
</section>

<style>
	.video-director {
		container-type: inline-size;
		container-name: video-director;
	}
</style>
