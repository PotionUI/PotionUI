<script lang="ts">
	import type { VideoDirectorValue, DirectorCapabilities, DirectorMediaValue } from '$lib/types/videoDirector';
	import type { StageKeyframeModel } from './stageModel';
	import { withChainKeyframeMedia, withTimelineKeyframeMedia, withKeyframeStrength, withChainEdgeKeyframeMedia, withChainEdgeKeyframeStrength, mediaFileLabel } from './stageModel';
	import { isChainEdgeKeyframeId, resolveDirectorTimingProfile } from '$lib/utils/videoDirector';
	import { withChainKeyframeAt, withTimelineKeyframeAt, isKeyframeLocked, deriveRailModel, chainFilmSecondsFromLocal } from './railModel';
	import { clamp } from '../timelineCore';
	import DirectorMediaSlot from '../DirectorMediaSlot.svelte';
	import StageCard from './StageCard.svelte';
	import StageField from './StageField.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Badge, IconButton } from '$lib/components/ui';

	let {
		model,
		doc,
		caps,
		timelineShotId,
		formData,
		onDoc
	}: {
		model: StageKeyframeModel;
		doc: VideoDirectorValue;
		caps: DirectorCapabilities;
		timelineShotId: string;
		formData: Record<string, unknown> | null | undefined;
		onDoc: (next: VideoDirectorValue) => void;
	} = $props();

	let isChainEdge = $derived(isChainEdgeKeyframeId(model.id));
	let isChain = $derived(model.role === 'keyframe');
	let locked = $derived(isKeyframeLocked(model.role));
	let roleLabel = $derived(model.role === 'first' ? 'Start' : model.role === 'last' ? 'End' : model.role === 'keyframe' ? 'Anywhere' : 'Free');
	let sourceLabel = $derived(mediaFileLabel(model.media));

	function setMedia(value: DirectorMediaValue | null) {
		if (isChainEdge) {
			onDoc(withChainEdgeKeyframeMedia(doc, model.id, value));
		} else if (isChain) {
			onDoc(withChainKeyframeMedia(doc, model.id, value));
		} else {
			onDoc(withTimelineKeyframeMedia(doc, timelineShotId, model.id, model.role as 'first' | 'last' | 'free', model.atSeconds, value));
		}
	}
	function applyTime(seconds: number) {
		if (isChain) {
			if (!model.landing) return;
			const rail = deriveRailModel(doc, caps, undefined, resolveDirectorTimingProfile(caps, formData));
			const clamped = clamp(seconds, 0, rail.fps > 0 ? model.landing.localTotalFrames / rail.fps : 0);
			onDoc(withChainKeyframeAt(doc, model.id, chainFilmSecondsFromLocal(rail, model.landing.shotIndex, clamped)));
		} else {
			const rail = deriveRailModel(doc, caps, timelineShotId);
			const clamped = clamp(seconds, 0, rail.fps > 0 ? model.totalFrames / rail.fps : 0);
			onDoc(withTimelineKeyframeAt(doc, timelineShotId, model.id, clamped));
		}
	}
	function setTime(e: Event) {
		const seconds = parseFloat((e.currentTarget as HTMLInputElement).value);
		if (!Number.isFinite(seconds)) return;
		applyTime(seconds);
	}
	function snapTo(atSeconds: number) {
		applyTime(atSeconds);
	}
	function setStrength(strength: number) {
		if (isChainEdge) {
			onDoc(withChainEdgeKeyframeStrength(doc, model.id, strength));
		} else {
			onDoc(withKeyframeStrength(doc, caps, timelineShotId, model.id, strength));
		}
	}
	function remove() {
		setMedia(null);
	}
</script>

<div class="stage-kf">
	<StageCard>
		{#snippet title()}
			<span class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle">Keyframe</span>
			<Badge size="sm">{roleLabel}</Badge>
			<span class="font-mono text-2xs tabular-nums text-fg-subtle">{model.atSeconds.toFixed(2)} s</span>
		{/snippet}
		{#snippet actions()}
			{#if !locked || model.media}
				<Tooltip text="Remove keyframe" position="top">
					<IconButton icon="close" label="Remove keyframe" size="sm" onclick={remove} />
				</Tooltip>
			{/if}
		{/snippet}
		{#snippet media()}
			<DirectorMediaSlot name="{model.id}-media" value={model.media} {formData} kind="image" fill onChange={setMedia} config={{ accept: 'image/*' }} />
		{/snippet}

		<div class="stage-kf-fields">
			<StageField label="Time">
				{#if locked}
					<span class="font-mono text-xs tabular-nums text-fg">{model.atSeconds.toFixed(2)} s</span>
				{:else}
					<input class="input h-8 w-24 font-mono text-xs tabular-nums" value={model.atSeconds.toFixed(2)} onchange={setTime} />
					{#if model.snapTargets.length > 0}
						<div class="mt-1.5 flex flex-wrap items-center gap-1.5">
							<span class="font-mono text-2xs uppercase tracking-[0.05em] text-fg-subtle">Snap to</span>
							{#each model.snapTargets as target (target.label)}
								<button
									type="button"
									class="snap-chip"
									class:active={model.snapped && model.snappedToLabel === target.label}
									onclick={() => snapTo(target.atSeconds)}
								>
									{target.label}
									<span class="font-mono tabular-nums">{target.atSeconds.toFixed(2)}s</span>
								</button>
							{/each}
						</div>
					{/if}
				{/if}
			</StageField>

			{#if sourceLabel}
				<StageField label="Source">
					<span class="truncate text-xs text-fg-muted">{sourceLabel}</span>
				</StageField>
			{/if}

			<StageField label="Strength" class="stage-kf-strength-field">
				<div class="flex items-center gap-2">
					<input
						type="range"
						min="0"
						max="1"
						step="0.01"
						class="strength-slider h-2 flex-1 cursor-pointer appearance-none rounded-lg bg-surface-3 accent-signal"
						disabled={!model.media}
						value={model.strength}
						oninput={(e) => setStrength(parseFloat((e.currentTarget as HTMLInputElement).value))}
					/>
					<span class="w-12 shrink-0 text-right font-mono text-xs tabular-nums text-fg-muted">{model.strength.toFixed(2)}</span>
				</div>
			</StageField>
		</div>
	</StageCard>
</div>

<style>
	.stage-kf-fields {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 12px;
	}

	:global(.stage-kf-strength-field) {
		grid-column: 1 / -1;
	}

	.snap-chip {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		height: 22px;
		padding: 0 8px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong));
		background: rgb(var(--surface-2));
		font-family: 'IBM Plex Mono', monospace;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: rgb(var(--fg-muted));
		cursor: pointer;
	}
	.snap-chip:hover {
		color: rgb(var(--fg));
		border-color: rgb(var(--line-hover));
	}
	.snap-chip.active {
		color: rgb(var(--signal));
		border-color: rgb(var(--signal) / 0.5);
		background: rgb(var(--signal) / 0.1);
	}
	.strength-slider:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
</style>
