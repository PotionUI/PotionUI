<script lang="ts">
	// A timed keyframe (chain 'anywhere' or timeline), staged: the media on
	// the left, the landing sentence and snap chips in the middle, the same
	// labelled footer strip on every profile.
	import type { VideoDirectorValue, DirectorCapabilities, DirectorMediaValue } from '$lib/types/videoDirector';
	import type { StageKeyframeModel } from './stageModel';
	import {
		withChainKeyframeMedia,
		withTimelineKeyframeMedia,
		withKeyframeStrength,
		withChainEdgeKeyframeMedia,
		withChainEdgeKeyframeStrength
	} from './stageModel';
	import { isChainEdgeKeyframeId } from '$lib/utils/videoDirector';
	import { withChainKeyframeAt, withTimelineKeyframeAt, isKeyframeLocked } from './railModel';
	import DirectorMediaSlot from '../DirectorMediaSlot.svelte';
	import { IconButton } from '$lib/components/ui';

	let {
		model,
		doc,
		caps,
		formData,
		onDoc
	}: {
		model: StageKeyframeModel;
		doc: VideoDirectorValue;
		caps: DirectorCapabilities;
		formData: Record<string, unknown> | null | undefined;
		onDoc: (next: VideoDirectorValue) => void;
	} = $props();

	let isChainEdge = $derived(isChainEdgeKeyframeId(model.id));
	let isChain = $derived(model.role === 'keyframe');
	// Mirrors KeyframesLane's drag lock (isKeyframeLocked) -- the "Snap to"
	// reposition affordance below must not offer a way around it.
	let locked = $derived(isKeyframeLocked(model.role));

	function setMedia(value: DirectorMediaValue | null) {
		if (isChainEdge) {
			onDoc(withChainEdgeKeyframeMedia(doc, model.id, value));
		} else if (isChain) {
			onDoc(withChainKeyframeMedia(doc, model.id, value));
		} else {
			onDoc(withTimelineKeyframeMedia(doc, model.id, model.role as 'first' | 'last' | 'free', model.atSeconds, value));
		}
	}
	function snapTo(atSeconds: number) {
		onDoc(isChain ? withChainKeyframeAt(doc, model.id, atSeconds) : withTimelineKeyframeAt(doc, model.id, atSeconds));
	}
	function setStrength(strength: number) {
		if (isChainEdge) {
			onDoc(withChainEdgeKeyframeStrength(doc, model.id, strength));
		} else {
			onDoc(withKeyframeStrength(doc, caps, model.id, strength));
		}
	}
	function remove() {
		setMedia(null);
	}
</script>

<div class="flex flex-col gap-3.5">
	<div class="flex items-center gap-2.5">
		<span class="rounded bg-signal px-1.5 py-0.5 font-mono text-2xs font-semibold uppercase tracking-wide text-canvas">Keyframe</span>
		<span class="text-sm font-semibold text-fg">{model.label}</span>
		<div class="flex-1"></div>
		<IconButton icon="trash" label="Remove keyframe" size="sm" onclick={remove} />
	</div>

	<div class="flex items-start gap-5">
		<div class="w-[190px] flex-shrink-0 self-stretch">
			<DirectorMediaSlot name="{model.id}-media" value={model.media} {formData} kind="image" fill onChange={setMedia} config={{ accept: 'image/*' }} />
		</div>

		<div class="min-w-0 flex-1">
			<div class="text-sm leading-6 text-fg">
				Lands <span class="font-mono">{model.atSeconds.toFixed(2)} s</span> into the video — frame
				<span class="font-mono">{model.atFrame}</span> of {model.totalFrames}
				{#if model.landing}
					, inside {model.landing.shotLabel} at that shot's own frame <span class="font-mono">{model.landing.localFrame}</span> of {model.landing.localTotalFrames}.
				{:else}
					.
				{/if}
			</div>

			{#if locked}
				<div class="mt-3.5 text-xs text-fg-subtle">Locked to this shot's edge — clear it to remove the frame instead of moving it.</div>
			{:else}
				<div class="mt-3.5 flex flex-wrap items-center gap-2">
					<span class="mr-0.5 font-mono text-2xs uppercase tracking-wide text-fg-subtle">Snap to</span>
					{#each model.snapTargets as target (target.label)}
						<button
							type="button"
							class="flex items-center gap-1.5 rounded px-2.5 py-1 text-xs text-fg-muted ring-1 ring-inset ring-line transition-colors hover:text-fg hover:ring-signal"
							onclick={() => snapTo(target.atSeconds)}
						>
							{target.label}
							<span class="font-mono text-2xs tabular-nums text-fg-subtle">{target.atSeconds.toFixed(2)} s</span>
						</button>
					{/each}
				</div>

				<div class="mt-2.5 text-xs text-fg-subtle">
					{model.snapped ? `Snapped to ${model.snappedToLabel}.` : 'Free right now — nudge on the rail, or pick a target above.'}
				</div>
			{/if}

			<label class="mt-3.5 flex items-center gap-2 text-xs">
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

	<div class="flex flex-wrap items-stretch overflow-hidden rounded-lg border border-line-strong bg-canvas shadow-well">
		<div class="min-w-[132px] border-r border-surface-2 px-3.5 py-2">
			<div class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Item</div>
			<div class="mt-0.5 text-xs text-fg">Keyframe · {model.role}</div>
		</div>
		<div class="min-w-[104px] border-r border-surface-2 px-3.5 py-2">
			<div class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">At</div>
			<div class="mt-0.5 font-mono text-xs tabular-nums text-fg">{model.atSeconds.toFixed(2)} s</div>
		</div>
		<div class="min-w-[104px] border-r border-surface-2 px-3.5 py-2">
			<div class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Frame</div>
			<div class="mt-0.5 font-mono text-xs tabular-nums text-fg">{model.atFrame} <span class="text-fg-subtle">/ {model.totalFrames}</span></div>
		</div>
		{#if model.landing}
			<div class="min-w-[190px] border-r border-surface-2 px-3.5 py-2">
				<div class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Lands in</div>
				<div class="mt-0.5 text-xs text-fg">{model.landing.shotLabel} <span class="font-mono text-fg-subtle">· local frame {model.landing.localFrame} / {model.landing.localTotalFrames}</span></div>
			</div>
		{/if}
		<div class="min-w-[110px] px-3.5 py-2">
			<div class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Keyframes</div>
			<div class="mt-0.5 font-mono text-xs tabular-nums text-fg">{model.countOfKeyframes} <span class="text-fg-subtle">/ {model.maxKeyframes}</span></div>
		</div>
	</div>
</div>
