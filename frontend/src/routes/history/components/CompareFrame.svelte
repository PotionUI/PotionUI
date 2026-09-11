<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import MediaPreview from '$lib/components/MediaPreview.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import type { GenerationFile } from '$lib/types/history';
	import {
		COMPARE_DEFAULT,
		compareValueFromKey,
		compareValueFromPointer,
		type CompareMode
	} from './compareFrame';

	// Shared by HistoryCompareModal (inline, compact) and its full-size viewer
	// (fullscreen, same props) so overlay/wipe interactivity only exists once.
	export let mode: CompareMode = 'side-by-side';
	export let leftFile: GenerationFile | null = null;
	export let rightFile: GenerationFile | null = null;
	export let leftGenerationId: string;
	export let rightGenerationId: string;
	export let leftLabel = 'A';
	export let rightLabel = 'B';
	export let value: number = COMPARE_DEFAULT;
	export let boxClass = 'aspect-square w-full';
	/** Off inside the full-size viewer, which already shows this frame at full size. */
	export let showExpand = true;

	const dispatch = createEventDispatcher<{
		valuechange: number;
		expand: 'left' | 'right' | 'composed';
	}>();

	let wipeFrameEl: HTMLDivElement;
	let dragging = false;

	function valueFromPointerEvent(e: PointerEvent): number {
		if (!wipeFrameEl) return value;
		return compareValueFromPointer(e.clientX, wipeFrameEl.getBoundingClientRect());
	}

	function handleWipePointerDown(e: PointerEvent) {
		dragging = true;
		(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
		dispatch('valuechange', valueFromPointerEvent(e));
	}
	function handleWipePointerMove(e: PointerEvent) {
		if (!dragging) return;
		dispatch('valuechange', valueFromPointerEvent(e));
	}
	function handleWipePointerUp(e: PointerEvent) {
		dragging = false;
		const el = e.currentTarget as HTMLElement;
		if (el.hasPointerCapture?.(e.pointerId)) el.releasePointerCapture(e.pointerId);
	}
	function handleHandleKeydown(e: KeyboardEvent) {
		const next = compareValueFromKey(value, e.key, e.shiftKey);
		if (next === null) return;
		e.preventDefault();
		dispatch('valuechange', next);
	}
	function handleOverlayInput(e: Event) {
		dispatch('valuechange', Number((e.target as HTMLInputElement).value));
	}
</script>

{#if mode === 'side-by-side'}
	<div class="grid grid-cols-2 gap-3">
		{#each [
			{ file: leftFile, id: leftGenerationId, side: 'left' as const },
			{ file: rightFile, id: rightGenerationId, side: 'right' as const }
		] as pane (pane.side)}
			<div class="relative {boxClass} rounded-lg overflow-hidden bg-black flex items-center justify-center">
				{#if pane.file}
					<MediaPreview
						file={pane.file}
						generationId={pane.id}
						thumbnailSize="large"
						loadFullOnClick={false}
						startFullLoaded
						fit="contain"
						className="w-full h-full"
					/>
					{#if showExpand}
						<div class="absolute bottom-2 right-2">
							<Tooltip text="Full size">
								<button
									type="button"
									class="corner-expand"
									aria-label="Full size"
									on:click={() => dispatch('expand', pane.side)}
								>
									<svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											d="M4 9V5a1 1 0 011-1h4M15 4h4a1 1 0 011 1v4M20 15v4a1 1 0 01-1 1h-4M9 20H5a1 1 0 01-1-1v-4"
										/>
									</svg>
								</button>
							</Tooltip>
						</div>
					{/if}
				{:else}
					<span class="text-xs text-fg-subtle">No preview</span>
				{/if}
			</div>
		{/each}
	</div>
{:else}
	<div class="flex flex-col gap-2">
		<div
			bind:this={wipeFrameEl}
			class="relative {boxClass} rounded-lg overflow-hidden bg-black select-none {mode === 'wipe'
				? 'cursor-ew-resize'
				: ''}"
			on:pointerdown={mode === 'wipe' ? handleWipePointerDown : undefined}
			on:pointermove={mode === 'wipe' ? handleWipePointerMove : undefined}
			on:pointerup={mode === 'wipe' ? handleWipePointerUp : undefined}
			on:pointercancel={mode === 'wipe' ? handleWipePointerUp : undefined}
		>
			<div class="absolute inset-0">
				{#if leftFile}
					<MediaPreview
						file={leftFile}
						generationId={leftGenerationId}
						thumbnailSize="large"
						loadFullOnClick={false}
						startFullLoaded
						fit="contain"
						className="w-full h-full"
					/>
				{/if}
			</div>
			<div
				class="absolute inset-0"
				style={mode === 'overlay' ? `opacity:${value / 100}` : `clip-path: inset(0 0 0 ${value}%)`}
			>
				{#if rightFile}
					<MediaPreview
						file={rightFile}
						generationId={rightGenerationId}
						thumbnailSize="large"
						loadFullOnClick={false}
						startFullLoaded
						fit="contain"
						className="w-full h-full"
					/>
				{/if}
			</div>

			<span
				class="absolute top-2 left-2 font-mono text-2xs uppercase tracking-[0.07em] text-white bg-black/50 rounded px-1.5 py-0.5 pointer-events-none"
			>
				{leftLabel}
			</span>
			<span
				class="absolute top-2 right-2 font-mono text-2xs uppercase tracking-[0.07em] text-white bg-black/50 rounded px-1.5 py-0.5 pointer-events-none"
			>
				{rightLabel}
			</span>

			{#if mode === 'wipe'}
				<div class="absolute inset-y-0 w-px bg-line-strong pointer-events-none" style="left: {value}%" />
				<div
					class="wipe-handle absolute top-1/2 -translate-y-1/2 -translate-x-1/2 flex items-center justify-center h-8 w-8 rounded-full bg-signal border-2 border-canvas shadow-floating cursor-ew-resize"
					style="left: {value}%"
					role="slider"
					aria-label="Wipe position"
					aria-valuemin="0"
					aria-valuemax="100"
					aria-valuenow={value}
					tabindex="0"
					on:keydown={handleHandleKeydown}
				>
					<svg class="w-3.5 h-3.5 text-canvas" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" d="M8 5l-5 7 5 7M16 5l5 7-5 7" />
					</svg>
				</div>
			{/if}

			{#if showExpand}
				<div class="absolute bottom-2 right-2">
					<Tooltip text="Full size">
						<button
							type="button"
							class="corner-expand"
							aria-label="Full size"
							on:pointerdown|stopPropagation
							on:click={() => dispatch('expand', 'composed')}
						>
							<svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									d="M4 9V5a1 1 0 011-1h4M15 4h4a1 1 0 011 1v4M20 15v4a1 1 0 01-1 1h-4M9 20H5a1 1 0 01-1-1v-4"
								/>
							</svg>
						</button>
					</Tooltip>
				</div>
			{/if}
		</div>

		{#if mode === 'overlay'}
			<div class="flex items-center gap-3 px-1">
				<span class="font-mono text-2xs text-fg-subtle shrink-0">A &#9666; &#9656; B</span>
				<input
					type="range"
					min="0"
					max="100"
					step="1"
					{value}
					on:input={handleOverlayInput}
					class="compare-range flex-1"
					aria-label="Overlay blend from A to B"
				/>
				<span class="font-mono text-2xs tabular-nums text-fg-subtle w-8 text-right">{value}</span>
			</div>
		{/if}
	</div>
{/if}

<style>
	.corner-expand {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		padding: 0.375rem;
		border-radius: 4px;
		color: rgb(255 255 255 / 0.85);
		background: rgb(0 0 0 / 0.5);
		transition: background-color 100ms;
	}
	.corner-expand:hover {
		background: rgb(0 0 0 / 0.7);
		color: white;
	}

	.wipe-handle:focus-visible {
		outline: 2px solid rgb(var(--signal));
		outline-offset: 2px;
	}

	.compare-range {
		appearance: none;
		-webkit-appearance: none;
		height: 4px;
		border-radius: 2px;
		background: rgb(var(--line-strong));
	}
	.compare-range::-webkit-slider-thumb {
		-webkit-appearance: none;
		width: 14px;
		height: 14px;
		border-radius: 50%;
		background: rgb(var(--signal));
		border: 2px solid rgb(var(--canvas));
		cursor: pointer;
	}
	.compare-range::-moz-range-thumb {
		width: 14px;
		height: 14px;
		border-radius: 50%;
		background: rgb(var(--signal));
		border: 2px solid rgb(var(--canvas));
		cursor: pointer;
	}
	.compare-range::-moz-range-track {
		height: 4px;
		border-radius: 2px;
		background: rgb(var(--line-strong));
	}
</style>
