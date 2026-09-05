<script lang="ts">
	// Top-level Video Director editor: modeless -- there is no mode switch.
	// The Shot Console (W1's rework of the old Stage+Rail composition) is the
	// body; this file owns the section wrapper, the container query the
	// console's 1440 layout keys off, AND the ONE header (maintainer ruling
	// 09-04: "I don't want to have two headers") -- title, the console's own
	// derived info strip (shot count/duration, readiness, capability chips,
	// via ConsoleHeader.svelte), and the Variables entry point (moved back
	// from PromptSection.svelte's standalone row, which now renders it only
	// for Prompt Relay). ShotConsole.svelte renders no header row of its own
	// any more -- it starts at the film rows -- and mirrors its derived
	// header up here via `onHeaderChange` so there is still only ONE place
	// (`deriveConsoleModel`, inside ShotConsole, against its own live `doc`)
	// that ever derives it.
	//
	// The document itself (the `lastEmitted` re-entrancy guard, `mode` kept
	// coherent via `deriveDirectorMode`) and all selection state live in
	// ShotConsole.svelte -- see that file's own header note.
	import type { VideoDirectorValue, DirectorCapabilities } from '$lib/types/videoDirector';
	import type { DirectorRunState } from '$lib/types/tabs';
	import type { ConsoleHeader as ConsoleHeaderModel } from './console/consoleModel';
	import ShotConsole from './console/ShotConsole.svelte';
	import ConsoleHeader from './console/ConsoleHeader.svelte';
	import Icon from '$lib/components/Icon.svelte';

	let {
		value,
		capabilities,
		presetId,
		selectedVariant = null,
		selectedMode = null,
		formData,
		runs,
		onChange,
		onOpenVariables,
		variableCount = 0,
		onCheckedChange,
		onGenerateShots
	}: {
		value: VideoDirectorValue | undefined;
		capabilities: DirectorCapabilities;
		presetId: string;
		/** See ShotConsole.svelte's own doc comments -- passed straight through
		 *  (part of the shot input identity's `generationContext`). */
		selectedVariant?: string | null;
		selectedMode?: string | null;
		/** The generate form's own field values -- threaded down so a Director
		 * media slot can offer "From form" (Stage B reference media). */
		formData: Record<string, unknown> | null | undefined;
		/** `Tab.directorRuns` -- see ShotConsole.svelte's own doc comment. */
		runs?: Record<string, DirectorRunState>;
		onChange: (v: VideoDirectorValue) => void;
		// Video Director renders its own header (unlike the prompt-relay/
		// segmented-prompt editors), so the Variables entry point that would
		// otherwise float in a standalone row above it lives here instead.
		// State and the modal itself stay owned by the caller (PromptSection.svelte).
		onOpenVariables?: () => void;
		variableCount?: number;
		/** See ShotConsole.svelte's own doc comments -- passed straight through. */
		onCheckedChange?: (checked: Set<string>) => void;
		onGenerateShots?: (shotIds: string[]) => void;
	} = $props();

	let header: ConsoleHeaderModel = $state({
		shotCount: 0,
		totalSeconds: 0,
		capChips: [],
		readiness: { ok: true, text: 'Ready' }
	});
</script>

<section class="video-director space-y-4" aria-label="Video Director">
	<header class="flex flex-wrap items-center gap-3 border-b border-line pb-3">
		<h2 class="text-lg font-semibold leading-tight text-fg">Video Director</h2>
		<div class="min-w-0 flex-1">
			<ConsoleHeader {header} />
		</div>
		{#if onOpenVariables}
			<button
				type="button"
				class="inline-flex h-8 flex-none items-center gap-1.5 rounded border border-line px-2.5 text-xs font-medium text-fg-muted transition-colors hover:border-line-hover hover:bg-surface-2 hover:text-fg"
				onclick={onOpenVariables}
			>
				<Icon name="braces" className="h-3.5 w-3.5" />
				<span>Variables</span>
				{#if variableCount > 0}
					<span class="rounded bg-signal/15 px-1.5 py-0.5 font-mono text-2xs tabular-nums text-signal">{variableCount}</span>
				{/if}
			</button>
		{/if}
	</header>

	<ShotConsole
		{value}
		{capabilities}
		{presetId}
		{selectedVariant}
		{selectedMode}
		{formData}
		{runs}
		{onChange}
		onHeaderChange={(h) => (header = h)}
		{onCheckedChange}
		{onGenerateShots}
	/>
</section>

<style>
	.video-director {
		container-type: inline-size;
		container-name: video-director;
	}
</style>
