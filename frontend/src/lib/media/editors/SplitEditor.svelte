<script lang="ts">
	/**
	 * Split into parts — audio only, cut into consecutive clips of a length the
	 * user enters in seconds.
	 *
	 * The player and waveform are lifted straight from `TrimEditor`'s audio
	 * branch so the two feel like one family; what differs is that there is no
	 * selection to drag, only a number to type, and no "replace" choice - a
	 * one-becomes-many edit can never take the original's place, so every part
	 * is always a new resource.
	 */
	import Waveform from '$lib/components/Waveform.svelte';
	import { resolvedTheme } from '$lib/stores/theme';
	import EditorShell from './EditorShell.svelte';
	import EditorSaveControls from './EditorSaveControls.svelte';
	import { safeDuration } from './trimPoints';
	import { formatClipLength, formatTimecode } from './timecode';
	import { describeSplitPlan, describeSplitRejection, toSplitPayload } from './splitPlan';
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

	let audioElement: HTMLAudioElement | null = null;
	let playhead = 0;
	// The element's own duration once it has one - metadata a row remembers can
	// be stale or absent, and the split is checked against the real thing.
	let probedDuration: number | null = null;

	$: duration = safeDuration(probedDuration ?? source.durationSeconds);

	/** Held as a string so the field never fights a mid-edit "10." or "". */
	let partSecondsText = '10';
	$: partSeconds = Number(partSecondsText);

	$: rejection = blockedReason ?? describeSplitRejection(partSeconds, duration);
	$: preview = rejection ? '' : describeSplitPlan(partSeconds, duration);

	function handleMetadata() {
		if (!audioElement) return;
		const reported = audioElement.duration;
		if (Number.isFinite(reported) && reported > 0) probedDuration = reported;
	}

	function handleTimeUpdate() {
		if (audioElement) playhead = audioElement.currentTime;
	}

	function seek(seconds: number) {
		if (!audioElement || duration <= 0) return;
		audioElement.currentTime = Math.max(0, Math.min(seconds, duration));
		playhead = audioElement.currentTime;
	}

	function save() {
		if (rejection) return;
		commit({ via: 'split', partSeconds: toSplitPayload(partSeconds) });
	}

	// The waveform paints to a canvas, which cannot read a CSS variable - the
	// same tokens the rest of the editor uses are resolved to values here, and
	// re-read a frame after a theme change so the canvas is not left painted in
	// the previous theme. Mirrors TrimEditor's palette sync exactly.
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
	title="Split into parts"
	fileName={source.fileName}
	icon="scissors"
	widthClass="md:w-[min(56rem,94vw)]"
	{busy}
	{onClose}
>
	<div class="flex flex-col gap-3 p-4 bg-canvas">
		<div class="rounded-lg border border-line bg-surface-1 px-3 pt-3 pb-2">
			<Waveform {audioElement} url={source.url} config={waveformConfig} onSeek={(time) => seek(time)} />
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

		<div class="flex flex-wrap items-center gap-2">
			<div class="flex flex-col gap-0.5 px-2.5 py-1.5 rounded bg-surface-1 ring-1 ring-inset ring-line">
				<span class="font-mono text-2xs uppercase tracking-[0.08em] text-fg-subtle">Duration</span>
				<span class="font-mono text-sm tabular-nums text-fg">
					{duration > 0 ? formatClipLength(duration) : formatTimecode(0)}
				</span>
			</div>

			<label
				for="split-part-seconds"
				class="flex flex-col gap-0.5 px-2.5 py-1.5 rounded bg-surface-1 ring-1 ring-inset ring-line"
			>
				<span class="font-mono text-2xs uppercase tracking-[0.08em] text-fg-subtle">
					Part length (seconds)
				</span>
				<input
					id="split-part-seconds"
					type="number"
					min="0"
					step="0.1"
					bind:value={partSecondsText}
					class="input w-24 h-6 px-1.5 py-0 min-h-0 font-mono text-sm tabular-nums text-fg"
				/>
			</label>

			<div class="min-w-0 flex-1">
				<p class="font-mono text-xs tabular-nums {rejection ? 'text-fg-muted' : 'text-success'}">
					{preview}
				</p>
			</div>
		</div>

		{#if resourceNote}
			<p class="text-2xs text-fg-muted">{resourceNote}</p>
		{/if}
	</div>

	<svelte:fragment slot="footer">
		<EditorSaveControls
			{busy}
			blockedReason={rejection}
			{failureMessage}
			allowReplace={false}
			applyLabel="Split"
			onCancel={onClose}
			onSave={save}
		/>
	</svelte:fragment>
</EditorShell>
