<script lang="ts">
	/**
	 * Extract a frame.
	 *
	 * The thing being picked is a frame, so the controls step in frames and the
	 * readout counts in frames - `frameTiming.ts` owns both conversions,
	 * including the one that bites: the last frame starts at `duration - 1/fps`,
	 * and `extract_video_frame` refuses `duration` itself as past the end.
	 *
	 * The element streams the byte range each seek needs. Pulling the clip into
	 * a blob to scrub it would mean waiting for the whole file first.
	 */
	import { onMount } from 'svelte';
	import EditorShell from './EditorShell.svelte';
	import EditorSaveControls from './EditorSaveControls.svelte';
	import { EDITOR_ICONS } from './editorIcons';
	import {
		clampFrameTime,
		formatFramePosition,
		frameDuration,
		lastFrameTime,
		safeFps
	} from './frameTiming';
	import { formatPreciseTime, formatTimecode } from './timecode';
	import { fractionOfTime, safeDuration, timeAtFraction } from './trimPoints';
	import { seekVideo } from './seekVideo';
	import type { EditorCommitFn, MediaEditorSource } from './types';

	export let source: MediaEditorSource;
	export let busy: boolean = false;
	export let blockedReason: string | null = null;
	/** What the editors had to do to give this media a resource, if anything. */
	export let resourceNote: string | null = null;
	/** Why the last save attempt failed, surfaced in the footer. */
	export let failureMessage: string | null = null;
	export let onClose: () => void;
	export let commit: EditorCommitFn;
	/** Where the playhead should open, when another editor handed it over. */
	export let startTime: number = 0;

	let videoElement: HTMLVideoElement | null = null;
	let railElement: HTMLDivElement | null = null;
	let probedDuration: number | null = null;
	let probedFps: number | null = null;
	let time = 0;
	let seeded = false;

	$: duration = safeDuration(probedDuration ?? source.durationSeconds);
	$: fps = safeFps(probedFps ?? source.fps);
	$: fraction = fractionOfTime(time, duration);
	$: rejection = blockedReason ?? (duration <= 0 ? 'Waiting for the clip to report its length…' : null);

	onMount(() => {
		time = Math.max(0, startTime);
	});

	function handleMetadata() {
		if (!videoElement) return;
		if (Number.isFinite(videoElement.duration) && videoElement.duration > 0) {
			probedDuration = videoElement.duration;
		}
		if (!seeded) {
			seeded = true;
			goTo(time);
		}
	}

	function goTo(seconds: number) {
		const next = clampFrameTime(seconds, duration, fps);
		time = next;
		if (videoElement) {
			void seekVideo(videoElement, next).catch(() => {
				// A seek that will not land is reported by the save attempt, which
				// is the only place it costs the user anything.
			});
		}
	}

	function step(frames: number) {
		goTo(time + frames * frameDuration(fps));
	}

	function scrub(event: PointerEvent) {
		if (!railElement) return;
		const box = railElement.getBoundingClientRect();
		if (box.width <= 0) return;
		goTo(timeAtFraction((event.clientX - box.left) / box.width, duration));
	}

	function dragScrub(event: PointerEvent) {
		if (event.button !== 0) return;
		event.preventDefault();
		scrub(event);
		const move = (moveEvent: PointerEvent) => scrub(moveEvent);
		const up = () => {
			window.removeEventListener('pointermove', move);
			window.removeEventListener('pointerup', up);
			window.removeEventListener('pointercancel', up);
		};
		window.addEventListener('pointermove', move);
		window.addEventListener('pointerup', up);
		window.addEventListener('pointercancel', up);
	}

	function handleKeydown(event: KeyboardEvent) {
		const frames = event.shiftKey ? 10 : 1;
		if (event.key === 'ArrowLeft') {
			event.preventDefault();
			step(-frames);
		} else if (event.key === 'ArrowRight') {
			event.preventDefault();
			step(frames);
		} else if (event.key === 'Home') {
			event.preventDefault();
			goTo(0);
		} else if (event.key === 'End') {
			event.preventDefault();
			goTo(lastFrameTime(duration, fps));
		}
	}

	function save() {
		commit({ via: 'frame', timeSeconds: clampFrameTime(time, duration, fps) });
	}
</script>

<EditorShell
	title="Extract a frame"
	fileName={source.fileName}
	icon="frame"
	widthClass="md:w-[min(52rem,94vw)]"
	{busy}
	{onClose}
>
	<div class="flex flex-col gap-3 p-4 bg-canvas">
		<div class="relative rounded-lg overflow-hidden bg-surface-2">
			<!-- svelte-ignore a11y-media-has-caption -->
			<video
				bind:this={videoElement}
				src={source.url}
				class="w-full max-h-[52vh] bg-surface-2 object-contain"
				preload="metadata"
				playsinline
				muted
				on:loadedmetadata={handleMetadata}
			>
				<track kind="captions" />
			</video>
			<span
				class="absolute top-2 left-2 px-1.5 py-0.5 rounded bg-canvas/75 font-mono text-2xs tabular-nums text-fg"
			>
				{formatTimecode(time)} / {formatTimecode(duration)}
			</span>
		</div>

		<div
			bind:this={railElement}
			role="presentation"
			class="relative h-9 rounded overflow-hidden bg-surface-2 ring-1 ring-inset ring-line-strong cursor-ew-resize"
			on:pointerdown={dragScrub}
		>
			<div
				class="absolute inset-y-0 left-0 bg-signal/15 pointer-events-none"
				style="width: {fraction * 100}%;"
			></div>
			<div
				class="absolute inset-y-0 w-0.5 -ml-px bg-signal pointer-events-none"
				style="left: {fraction * 100}%;"
			></div>
		</div>

		<div class="flex flex-wrap items-center gap-2">
			<div class="flex items-center gap-1">
				<button
					type="button"
					title="Previous frame — hold Shift for ten"
					aria-label="Previous frame"
					class="w-8 h-8 inline-flex items-center justify-center rounded border border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg transition-colors"
					on:click={() => step(-1)}
					on:keydown={handleKeydown}
				>
					<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="1.8"
							d={EDITOR_ICONS.stepBack}
						/>
					</svg>
				</button>
				<button
					type="button"
					title="Next frame — hold Shift for ten"
					aria-label="Next frame"
					class="w-8 h-8 inline-flex items-center justify-center rounded border border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg transition-colors"
					on:click={() => step(1)}
					on:keydown={handleKeydown}
				>
					<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="1.8"
							d={EDITOR_ICONS.stepForward}
						/>
					</svg>
				</button>
			</div>

			<div class="flex flex-col gap-0.5 px-2.5 py-1.5 rounded bg-surface-1 ring-1 ring-inset ring-line">
				<span class="font-mono text-2xs uppercase tracking-[0.08em] text-fg-subtle">Frame</span>
				<span class="font-mono text-sm tabular-nums text-fg">
					{formatFramePosition(time, duration, fps)}
				</span>
			</div>

			<div class="flex flex-col gap-0.5 px-2.5 py-1.5 rounded bg-surface-1 ring-1 ring-inset ring-line">
				<span class="font-mono text-2xs uppercase tracking-[0.08em] text-fg-subtle">At</span>
				<span class="font-mono text-sm tabular-nums text-success">{formatPreciseTime(time)}</span>
			</div>

			<p class="min-w-0 flex-1 text-2xs text-fg-subtle">
				Saved as a new image — the clip is left alone.
				{#if resourceNote}<br />{resourceNote}{/if}
			</p>
		</div>
	</div>

	<svelte:fragment slot="footer">
		<EditorSaveControls
			applyLabel="Save frame"
			allowReplace={false}
			{busy}
			blockedReason={rejection}
			{failureMessage}
			onCancel={onClose}
			onSave={save}
		/>
	</svelte:fragment>
</EditorShell>
