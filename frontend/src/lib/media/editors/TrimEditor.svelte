<script lang="ts">
	/**
	 * Trim in / out — video on a rail under the clip, audio on its waveform.
	 *
	 * One editor for both because the interaction is identical: two handles on a
	 * timeline, three readouts, one selection. Only the thing above the rail
	 * differs, and that difference is a single `{#if}`.
	 *
	 * A trim is a re-encode of a container, which a browser cannot do - so this
	 * editor always goes through the edit API and always needs a library
	 * resource behind it. When there is none, `blockedReason` says so instead of
	 * offering a button that cannot work.
	 *
	 * The clip is streamed by the element itself, never fetched into a blob:
	 * seeking a `<video>` relies on range requests, and a blob would mean
	 * downloading the whole file before the first scrub.
	 */
	import Waveform from '$lib/components/Waveform.svelte';
	import { resolvedTheme } from '$lib/stores/theme';
	import EditorShell from './EditorShell.svelte';
	import EditorSaveControls from './EditorSaveControls.svelte';
	import { EDITOR_ICONS } from './editorIcons';
	import {
		fractionOfTime,
		fullTrim,
		isFullClip,
		safeDuration,
		setTrimEnd,
		setTrimStart,
		timeAtFraction,
		toTrimOperation,
		trimLength,
		type TrimPoints
	} from './trimPoints';
	import { formatClipLength, formatPreciseTime, formatTimecode } from './timecode';
	import { describeRejection } from './editOperations';
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
	/** Video only: hands the clip to the frame editor at the current playhead. */
	export let onExtractFrame: ((timeSeconds: number) => void) | null = null;

	const isAudio = source.kind === 'audio';

	let audioElement: HTMLAudioElement | null = null;
	let videoElement: HTMLVideoElement | null = null;
	let railElement: HTMLDivElement | null = null;

	$: mediaElement = (isAudio ? audioElement : videoElement) as HTMLMediaElement | null;
	let playhead = 0;
	// The element's own duration once it has one - metadata a row remembers can
	// be stale or absent, and the trim is checked against the real thing.
	let probedDuration: number | null = null;

	$: duration = safeDuration(probedDuration ?? source.durationSeconds);
	let points: TrimPoints = { start: 0, end: 0 };
	let initialised = false;

	// Opens on the whole clip, once, as soon as there is a duration to open on.
	$: if (!initialised && duration > 0) {
		points = fullTrim(duration);
		initialised = true;
	}

	$: operations = duration > 0 && !isFullClip(points, duration) ? [toTrimOperation(points, duration)] : [];
	// The full-clip case is named before the generic "nothing to apply": both are
	// true of an untouched selection, and only one of them tells the user what
	// to do about it.
	$: rejection =
		blockedReason ??
		(duration <= 0 ? 'Waiting for the clip to report its length…' : null) ??
		(isFullClip(points, duration) ? 'Move a handle to choose what to keep.' : null) ??
		describeRejection(operations, isAudio ? 'audio' : 'video');

	$: startFraction = fractionOfTime(points.start, duration);
	$: endFraction = fractionOfTime(points.end, duration);
	$: playheadFraction = fractionOfTime(playhead, duration);

	$: readouts = [
		{ key: 'in', label: 'In', value: formatPreciseTime(points.start), tone: 'text-fg' },
		{ key: 'out', label: 'Out', value: formatPreciseTime(points.end), tone: 'text-fg' },
		{ key: 'len', label: 'Length', value: formatClipLength(trimLength(points)), tone: 'text-success' }
	];

	function handleMetadata() {
		if (!mediaElement) return;
		const reported = mediaElement.duration;
		if (Number.isFinite(reported) && reported > 0) probedDuration = reported;
	}

	function handleTimeUpdate() {
		if (mediaElement) playhead = mediaElement.currentTime;
	}

	function seek(seconds: number) {
		if (!mediaElement || duration <= 0) return;
		mediaElement.currentTime = Math.max(0, Math.min(seconds, duration));
		playhead = mediaElement.currentTime;
	}

	function fractionAt(clientX: number): number {
		if (!railElement) return 0;
		const box = railElement.getBoundingClientRect();
		if (box.width <= 0) return 0;
		return (clientX - box.left) / box.width;
	}

	function dragHandle(event: PointerEvent, which: 'in' | 'out') {
		if (event.button !== 0) return;
		event.preventDefault();
		event.stopPropagation();

		const move = (moveEvent: PointerEvent) => {
			const seconds = timeAtFraction(fractionAt(moveEvent.clientX), duration);
			points = which === 'in' ? setTrimStart(points, seconds, duration) : setTrimEnd(points, seconds, duration);
			seek(which === 'in' ? points.start : points.end);
		};
		const up = () => {
			window.removeEventListener('pointermove', move);
			window.removeEventListener('pointerup', up);
			window.removeEventListener('pointercancel', up);
		};

		window.addEventListener('pointermove', move);
		window.addEventListener('pointerup', up);
		window.addEventListener('pointercancel', up);
	}

	function scrub(event: PointerEvent) {
		seek(timeAtFraction(fractionAt(event.clientX), duration));
	}

	function nudgeHandle(which: 'in' | 'out', seconds: number) {
		points =
			which === 'in'
				? setTrimStart(points, points.start + seconds, duration)
				: setTrimEnd(points, points.end + seconds, duration);
	}

	function handleKeydown(event: KeyboardEvent, which: 'in' | 'out') {
		const step = event.shiftKey ? 1 : 0.1;
		if (event.key === 'ArrowLeft') {
			event.preventDefault();
			nudgeHandle(which, -step);
		} else if (event.key === 'ArrowRight') {
			event.preventDefault();
			nudgeHandle(which, step);
		}
	}

	/** Set the near handle to wherever the user has parked the playhead. */
	function markHere(which: 'in' | 'out') {
		points = which === 'in' ? setTrimStart(points, playhead, duration) : setTrimEnd(points, playhead, duration);
	}

	function save(mode: 'new' | 'replace') {
		commit({ via: 'operations', operations, mode });
	}

	// The waveform paints to a canvas, which cannot read a CSS variable - the
	// same tokens the rest of the editor uses are resolved to values here, and
	// re-read a frame after a theme change so the canvas is not left painted in
	// the previous theme.
	function readWaveformPalette() {
		if (typeof window === 'undefined') return waveformPalette;
		const styles = getComputedStyle(document.documentElement);
		const token = (name: string, fallback: string) => {
			const raw = styles.getPropertyValue(name).trim();
			return raw ? `rgb(${raw})` : fallback;
		};
		return {
			waveColor: token('--line-strong', 'rgb(120 120 120)'),
			progressColor: token('--signal', 'rgb(77 159 255)'),
			cursorColor: token('--fg', 'rgb(230 230 230)')
		};
	}

	let waveformPalette = {
		waveColor: 'rgb(120 120 120)',
		progressColor: 'rgb(77 159 255)',
		cursorColor: 'rgb(230 230 230)'
	};

	// `theme` is the dependency this refresh exists for, not an input.
	function refreshWaveformPalette(theme: string) {
		if (!theme || typeof requestAnimationFrame !== 'function') {
			waveformPalette = readWaveformPalette();
			return;
		}
		requestAnimationFrame(() => {
			waveformPalette = readWaveformPalette();
		});
	}

	$: refreshWaveformPalette($resolvedTheme);
	$: waveformConfig = {
		height: 120,
		backgroundColor: 'transparent',
		barWidth: 2,
		barGap: 1,
		barRadius: 1,
		...waveformPalette
	};
</script>

<EditorShell
	title={isAudio ? 'Trim on waveform' : 'Trim in / out'}
	fileName={source.fileName}
	icon="scissors"
	widthClass="md:w-[min(56rem,94vw)]"
	{busy}
	{onClose}
>
	<div class="flex flex-col gap-3 p-4 bg-canvas">
		{#if isAudio}
			<div class="rounded-lg border border-line bg-surface-1 px-3 pt-3 pb-2">
				<Waveform
					{audioElement}
					url={source.url}
					config={waveformConfig}
					onSeek={(time) => seek(time)}
				/>
				<!-- svelte-ignore a11y-media-has-caption -->
				<audio
					bind:this={audioElement}
					src={source.url}
					class="w-full mt-2"
					controls
					preload="metadata"
					on:loadedmetadata={handleMetadata}
					on:timeupdate={handleTimeUpdate}
				></audio>
			</div>
		{:else}
			<div class="relative rounded-lg overflow-hidden bg-surface-2">
				<!-- svelte-ignore a11y-media-has-caption -->
				<video
					bind:this={videoElement}
					src={source.url}
					class="w-full max-h-[46vh] bg-surface-2 object-contain"
					controls
					preload="metadata"
					on:loadedmetadata={handleMetadata}
					on:timeupdate={handleTimeUpdate}
				>
					<track kind="captions" />
				</video>
				<span
					class="absolute top-2 left-2 px-1.5 py-0.5 rounded bg-canvas/75 font-mono text-2xs tabular-nums text-fg"
				>
					{formatTimecode(playhead)} / {formatTimecode(duration)}
				</span>
			</div>
		{/if}

		<!-- The rail. Everything outside the selection is dimmed; the handles are
		     the only things that move it. -->
		<div
			bind:this={railElement}
			role="presentation"
			class="relative h-14 rounded overflow-hidden bg-surface-2 ring-1 ring-inset ring-line-strong"
			on:pointerdown={scrub}
		>
			<div class="absolute inset-0 flex pointer-events-none">
				{#each Array(12) as _, tick (tick)}
					<div class="flex-1 border-r border-line last:border-r-0"></div>
				{/each}
			</div>

			<div
				class="absolute inset-y-0 left-0 bg-canvas/70 pointer-events-none"
				style="width: {startFraction * 100}%;"
			></div>
			<div
				class="absolute inset-y-0 right-0 bg-canvas/70 pointer-events-none"
				style="width: {(1 - endFraction) * 100}%;"
			></div>
			<div
				class="absolute inset-y-0 ring-2 ring-inset ring-signal pointer-events-none"
				style="left: {startFraction * 100}%; right: {(1 - endFraction) * 100}%;"
			></div>

			<div
				class="absolute inset-y-0 w-px bg-fg pointer-events-none"
				style="left: {playheadFraction * 100}%;"
			></div>

			<button
				type="button"
				aria-label="Trim in point"
				title="Drag, or use the arrow keys"
				class="absolute inset-y-0 w-3 -ml-1.5 rounded-[3px] bg-signal cursor-ew-resize"
				style="left: {startFraction * 100}%;"
				on:pointerdown={(event) => dragHandle(event, 'in')}
				on:keydown={(event) => handleKeydown(event, 'in')}
			>
				<span class="block mx-auto w-0.5 h-4 rounded bg-canvas/60"></span>
			</button>
			<button
				type="button"
				aria-label="Trim out point"
				title="Drag, or use the arrow keys"
				class="absolute inset-y-0 w-3 -ml-1.5 rounded-[3px] bg-signal cursor-ew-resize"
				style="left: {endFraction * 100}%;"
				on:pointerdown={(event) => dragHandle(event, 'out')}
				on:keydown={(event) => handleKeydown(event, 'out')}
			>
				<span class="block mx-auto w-0.5 h-4 rounded bg-canvas/60"></span>
			</button>
		</div>

		<div class="flex flex-wrap items-center gap-2">
			{#each readouts as readout (readout.key)}
				<div class="flex flex-col gap-0.5 px-2.5 py-1.5 rounded bg-surface-1 ring-1 ring-inset ring-line">
					<span class="font-mono text-2xs uppercase tracking-[0.08em] text-fg-subtle">
						{readout.label}
					</span>
					<span class="font-mono text-sm tabular-nums {readout.tone}">{readout.value}</span>
				</div>
			{/each}

			<div class="flex flex-col gap-1">
				<button
					type="button"
					class="h-6 px-2 rounded border border-line-strong bg-surface-2 font-mono text-2xs uppercase tracking-[0.06em] text-fg-muted hover:border-line-hover hover:text-fg transition-colors"
					on:click={() => markHere('in')}
				>
					Set in here
				</button>
				<button
					type="button"
					class="h-6 px-2 rounded border border-line-strong bg-surface-2 font-mono text-2xs uppercase tracking-[0.06em] text-fg-muted hover:border-line-hover hover:text-fg transition-colors"
					on:click={() => markHere('out')}
				>
					Set out here
				</button>
			</div>

			{#if resourceNote}
				<p class="min-w-0 flex-1 text-2xs text-fg-muted">{resourceNote}</p>
			{:else}
				<div class="flex-1"></div>
			{/if}

			{#if !isAudio && onExtractFrame}
				<button
					type="button"
					class="inline-flex items-center gap-2 h-8 px-3 rounded border border-line-strong bg-surface-2 text-xs text-fg-muted hover:border-line-hover hover:text-fg transition-colors"
					on:click={() => onExtractFrame?.(playhead)}
				>
					<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="1.8"
							d={EDITOR_ICONS.frame}
						/>
					</svg>
					Extract frame at playhead
				</button>
			{/if}
		</div>
	</div>

	<svelte:fragment slot="footer">
		<EditorSaveControls
			{busy}
			blockedReason={rejection}
			{failureMessage}
			onCancel={onClose}
			onSave={save}
		/>
	</svelte:fragment>
</EditorShell>
