<script lang="ts">
	// A join between two chain shots, staged: the two boundary frames, the
	// prose sentence that explains the arithmetic, and the overlap/stitch
	// controls. Both overlap and stitch are document-wide settings
	// (chain.continuation) rather than per-join -- the copy says so plainly
	// rather than implying an independent value per join.
	import type { VideoDirectorValue, DirectorCapabilities } from '$lib/types/videoDirector';
	import type { StageJoinModel } from './stageModel';
	import { withSeamKind, withOverlapFrames, withStitch } from './stageModel';
	import Icon from '$lib/components/Icon.svelte';
	import { Switch } from '$lib/components/ui';

	let {
		model,
		doc,
		caps,
		onDoc
	}: {
		model: StageJoinModel;
		doc: VideoDirectorValue;
		caps: DirectorCapabilities;
		onDoc: (next: VideoDirectorValue) => void;
	} = $props();

	function setKind(kind: 'continue' | 'cut') {
		onDoc(withSeamKind(doc, caps, model.id, kind));
	}
	function setOverlap(frames: number) {
		onDoc(withOverlapFrames(doc, caps, frames));
	}
	function setStitch(stitch: boolean) {
		onDoc(withStitch(doc, caps, stitch));
	}
</script>

<div class="flex flex-col gap-3.5">
	<div class="flex items-center gap-2.5">
		<span class="rounded bg-signal px-1.5 py-0.5 font-mono text-2xs font-semibold uppercase tracking-wide text-canvas">
			Join {model.beforeShotIndex + 1}→{model.beforeShotIndex + 2}
		</span>
		<span class="text-sm font-semibold text-fg">{model.isCut ? 'Hard cut' : 'Continue from tail'}</span>
		<div class="flex-1"></div>
		{#if model.continuationAvailable}
			<div class="flex gap-0.5 rounded-md bg-surface-1 p-0.5 ring-1 ring-inset ring-line">
				<button
					type="button"
					class="flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium {!model.isCut ? 'bg-surface-3 text-fg' : 'text-fg-muted hover:text-fg'}"
					onclick={() => setKind('continue')}
				>
					Continue
				</button>
				<button
					type="button"
					class="flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium {model.isCut ? 'bg-surface-3 text-fg' : 'text-fg-muted hover:text-fg'}"
					onclick={() => setKind('cut')}
				>
					Hard cut
				</button>
			</div>
		{:else}
			<span class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Independent cut · references mode</span>
		{/if}
	</div>

	<div class="flex items-stretch">
		<div class="w-[220px] flex-shrink-0 rounded-l-lg bg-canvas ring-1 ring-inset ring-line-strong">
			<div class="flex h-[130px] items-end justify-center p-2 text-2xs text-fg-subtle">{model.tailFrameLabel}</div>
		</div>
		<div class="flex w-[90px] flex-shrink-0 items-center justify-center bg-signal/10 ring-1 ring-inset ring-signal/40">
			{#if !model.isCut}
				<span class="rounded bg-canvas px-1.5 py-0.5 font-mono text-xs tabular-nums text-signal ring-1 ring-inset ring-signal/40">
					{model.overlapFrames} f
				</span>
			{:else}
				<Icon name="close" className="h-4 w-4 text-fg-subtle" />
			{/if}
		</div>
		<div class="w-[220px] flex-shrink-0 rounded-r-lg bg-canvas ring-1 ring-inset ring-line-strong">
			<div class="flex h-[130px] items-end justify-center p-2 text-2xs text-fg-subtle">{model.headFrameLabel}</div>
		</div>
	</div>

	<p class="max-w-[78ch] text-sm leading-6 text-fg-muted">{model.sentence}</p>

	{#if !model.isCut}
		<div class="flex items-center gap-4 rounded-lg border border-surface-2 bg-surface-1 px-3.5 py-2.5">
			<div class="w-32 flex-shrink-0">
				<div class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Overlap</div>
				<div class="mt-0.5 font-mono text-sm tabular-nums text-fg">{model.overlapFrames} f <span class="text-fg-subtle">· {model.overlapSeconds.toFixed(2)} s</span></div>
			</div>
			<input
				type="range"
				min="0"
				max={model.maxOverlapFrames}
				value={model.overlapFrames}
				class="flex-1 accent-signal"
				oninput={(e) => setOverlap(parseInt((e.currentTarget as HTMLInputElement).value, 10))}
			/>
			<div class="h-8 w-px flex-shrink-0 bg-line"></div>
			<div class="flex flex-shrink-0 items-center gap-2.5">
				<Switch checked={model.stitch} onchange={setStitch} label="Stitch" size="sm" />
				<div>
					<div class="text-xs text-fg">Stitch</div>
					<div class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">global · every join</div>
				</div>
			</div>
		</div>
	{/if}

	<div class="rounded-lg border border-surface-2 bg-canvas px-3.5 py-2.5">
		<div class="mb-1.5 font-mono text-2xs uppercase tracking-wide text-fg-subtle">Total, recomputed live</div>
		<div class="font-mono text-xs leading-6 tabular-nums text-fg-muted">
			{model.chainTotals.generatedFrames.join(' + ')} <span class="text-fg-subtle">frames generated</span><br />
			{#each model.chainTotals.deductions as d (d.seamLabel)}
				− {d.frames} <span class="text-fg-subtle">at join {d.seamLabel}{d.isCut ? ' (cut)' : ''}</span><br />
			{/each}
			<span class="text-fg">= {model.chainTotals.totalFrames} f · {model.chainTotals.totalSeconds.toFixed(2)} s @ {model.chainTotals.fps} fps</span>
		</div>
	</div>
</div>
