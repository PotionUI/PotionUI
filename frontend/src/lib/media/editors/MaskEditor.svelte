<script lang="ts">
	/**
	 * Create inpainting mask.
	 *
	 * A mask is not an edit of the image and never becomes a library resource:
	 * it travels on the `${name}_inpaint_mask` sibling channel, bound to the
	 * image it was painted on by `mediaLoaderMask.ts`. That binding keys on the
	 * stored PATH, never on a blob handle, and drops the mask the moment the
	 * field points at a different image - so this editor hands back the painted
	 * bytes and lets the field's existing owner do the rest rather than
	 * inventing a second mask identity.
	 *
	 * The convention on the wire is the one already on disk: an image the size
	 * of the source, WHITE where the model should repaint and black elsewhere.
	 *
	 * The strokes are kept on their own canvas rather than painted over the
	 * picture - the previous implementation recovered the mask by diffing the
	 * painted canvas against the original, which cannot tell a stroke from a
	 * white pixel that was already there.
	 */
	import { onMount } from 'svelte';
	import EditorShell from './EditorShell.svelte';
	import EditorSaveControls from './EditorSaveControls.svelte';
	import { EDITOR_ICONS } from './editorIcons';
	import { loadImage } from './loadImage';
	import type { EditorCommitFn, MediaEditorSource } from './types';

	export let source: MediaEditorSource;
	/** A mask already held for this image, so reopening resumes it. */
	export let existingMaskUrl: string | null = null;
	export let busy: boolean = false;
	/** Why the last save attempt failed, surfaced in the footer. */
	export let failureMessage: string | null = null;
	export let onClose: () => void;
	export let commit: EditorCommitFn;

	const MIN_BRUSH = 4;
	const MAX_BRUSH = 400;

	let canvasElement: HTMLCanvasElement | null = null;
	let stageElement: HTMLDivElement | null = null;
	let context: CanvasRenderingContext2D | null = null;
	let image: HTMLImageElement | null = null;
	let loadError: string | null = null;

	let brushSize = 64;
	let erasing = false;
	let painting = false;
	let hasStrokes = false;
	let lastPoint: { x: number; y: number } | null = null;

	let cursor = { x: 0, y: 0, visible: false };

	onMount(async () => {
		try {
			image = await loadImage(source.url);
		} catch {
			loadError = 'This image could not be opened for masking.';
			return;
		}

		if (!canvasElement) return;
		canvasElement.width = image.naturalWidth;
		canvasElement.height = image.naturalHeight;
		context = canvasElement.getContext('2d');
		if (!context) {
			loadError = 'This browser could not open a drawing surface.';
			return;
		}
		context.lineCap = 'round';
		context.lineJoin = 'round';

		if (existingMaskUrl) await restoreMask(existingMaskUrl);
	});

	/**
	 * Reload a held mask. The stored file is white-on-black; the working canvas
	 * is white-on-transparent, so the black has to be dropped rather than drawn
	 * - painting the file straight on would make the whole image masked.
	 */
	async function restoreMask(url: string) {
		if (!context || !canvasElement) return;
		try {
			const mask = await loadImage(url);
			const scratch = document.createElement('canvas');
			scratch.width = canvasElement.width;
			scratch.height = canvasElement.height;
			const scratchContext = scratch.getContext('2d');
			if (!scratchContext) return;

			scratchContext.drawImage(mask, 0, 0, scratch.width, scratch.height);
			const data = scratchContext.getImageData(0, 0, scratch.width, scratch.height);
			for (let i = 0; i < data.data.length; i += 4) {
				const lit = data.data[i] > 128;
				data.data[i] = 255;
				data.data[i + 1] = 255;
				data.data[i + 2] = 255;
				data.data[i + 3] = lit ? 255 : 0;
				if (lit) hasStrokes = true;
			}
			scratchContext.putImageData(data, 0, 0);
			context.drawImage(scratch, 0, 0);
		} catch {
			// A mask that will not load is not worth blocking a fresh one on.
		}
	}

	/** Pointer position in the canvas's own pixels, which is where strokes live. */
	function pointAt(event: PointerEvent): { x: number; y: number } | null {
		if (!canvasElement) return null;
		const box = canvasElement.getBoundingClientRect();
		if (box.width <= 0 || box.height <= 0) return null;
		return {
			x: ((event.clientX - box.left) / box.width) * canvasElement.width,
			y: ((event.clientY - box.top) / box.height) * canvasElement.height
		};
	}

	function strokeTo(point: { x: number; y: number }) {
		if (!context) return;
		context.globalCompositeOperation = erasing ? 'destination-out' : 'source-over';
		context.strokeStyle = 'rgb(255, 255, 255)';
		context.fillStyle = 'rgb(255, 255, 255)';
		context.lineWidth = brushSize;

		const from = lastPoint ?? point;
		context.beginPath();
		context.moveTo(from.x, from.y);
		context.lineTo(point.x, point.y);
		context.stroke();

		lastPoint = point;
		if (!erasing) hasStrokes = true;
	}

	function startPaint(event: PointerEvent) {
		if (event.button !== 0) return;
		const point = pointAt(event);
		if (!point) return;
		event.preventDefault();
		painting = true;
		lastPoint = null;
		strokeTo(point);
	}

	function movePaint(event: PointerEvent) {
		trackCursor(event);
		if (!painting) return;
		const point = pointAt(event);
		if (point) strokeTo(point);
	}

	function endPaint() {
		painting = false;
		lastPoint = null;
	}

	function trackCursor(event: PointerEvent) {
		if (!stageElement) return;
		const box = stageElement.getBoundingClientRect();
		cursor = { x: event.clientX - box.left, y: event.clientY - box.top, visible: true };
	}

	/** The brush drawn at screen scale, so its size means what it looks like. */
	$: displayBrush = (() => {
		if (!canvasElement || !stageElement) return brushSize;
		const box = canvasElement.getBoundingClientRect();
		if (!box.width || !canvasElement.width) return brushSize;
		return (brushSize * box.width) / canvasElement.width;
	})();

	function clearMask() {
		if (!context || !canvasElement) return;
		context.globalCompositeOperation = 'source-over';
		context.clearRect(0, 0, canvasElement.width, canvasElement.height);
		hasStrokes = false;
	}

	$: rejection = loadError ?? (hasStrokes ? null : 'Paint over the area to repaint.');

	function save() {
		if (!canvasElement) return;
		const output = document.createElement('canvas');
		output.width = canvasElement.width;
		output.height = canvasElement.height;
		const outputContext = output.getContext('2d');
		if (!outputContext) return;

		// Black everywhere, white where the strokes are: the convention the
		// generation side already reads.
		outputContext.fillStyle = 'rgb(0, 0, 0)';
		outputContext.fillRect(0, 0, output.width, output.height);
		outputContext.drawImage(canvasElement, 0, 0);

		commit({ via: 'mask', dataUrl: output.toDataURL('image/png') });
	}
</script>

<EditorShell
	title="Create inpainting mask"
	fileName={source.fileName}
	icon="brush"
	widthClass="md:w-[min(60rem,94vw)]"
	{busy}
	{onClose}
>
	<div class="flex flex-col md:flex-row items-stretch min-h-0">
		<div class="flex-1 min-w-0 flex items-center justify-center p-4 bg-canvas">
			{#if loadError}
				<p class="py-16 text-sm text-danger">{loadError}</p>
			{:else}
				<div
					bind:this={stageElement}
					role="presentation"
					class="relative max-h-[56vh] rounded overflow-hidden bg-surface-2"
					on:pointerleave={() => {
						cursor = { ...cursor, visible: false };
						endPaint();
					}}
				>
					<img
						src={source.url}
						alt={source.fileName}
						draggable="false"
						class="block max-h-[56vh] max-w-full select-none"
					/>
					<canvas
						bind:this={canvasElement}
						class="absolute inset-0 w-full h-full opacity-70 cursor-none touch-none"
						on:pointerdown={startPaint}
						on:pointermove={movePaint}
						on:pointerup={endPaint}
						on:pointercancel={endPaint}
					></canvas>

					{#if cursor.visible}
						<div
							class="absolute rounded-full ring-1 ring-fg/70 pointer-events-none"
							style="left: {cursor.x}px; top: {cursor.y}px; width: {displayBrush}px; height: {displayBrush}px; transform: translate(-50%, -50%);"
						></div>
					{/if}
				</div>
			{/if}
		</div>

		<div
			class="w-full md:w-60 shrink-0 flex flex-col gap-4 p-4 border-t md:border-t-0 md:border-l border-line bg-surface-1"
		>
			<div>
				<p class="mb-2 font-mono text-2xs uppercase tracking-[0.08em] text-fg-subtle">Tool</p>
				<div class="flex gap-1.5">
					<button
						type="button"
						class="flex-1 h-8 inline-flex items-center justify-center gap-1.5 rounded border text-xs transition-colors {erasing
							? 'border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg'
							: 'border-signal/60 bg-signal/10 text-signal'}"
						on:click={() => (erasing = false)}
					>
						<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="1.8"
								d={EDITOR_ICONS.brush}
							/>
						</svg>
						Paint
					</button>
					<button
						type="button"
						class="flex-1 h-8 inline-flex items-center justify-center gap-1.5 rounded border text-xs transition-colors {erasing
							? 'border-signal/60 bg-signal/10 text-signal'
							: 'border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg'}"
						on:click={() => (erasing = true)}
					>
						<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="1.8"
								d={EDITOR_ICONS.eraser}
							/>
						</svg>
						Erase
					</button>
				</div>
			</div>

			<div>
				<div class="flex items-baseline justify-between mb-2">
					<span class="font-mono text-2xs uppercase tracking-[0.08em] text-fg-subtle">Brush</span>
					<span class="font-mono text-2xs tabular-nums text-fg-muted">{brushSize}px</span>
				</div>
				<input
					type="range"
					min={MIN_BRUSH}
					max={MAX_BRUSH}
					bind:value={brushSize}
					aria-label="Brush size"
					class="w-full accent-signal"
				/>
			</div>

			<div class="h-px bg-line"></div>

			<p class="text-2xs text-fg-subtle">
				Paint over what the model should repaint. Everything you leave alone is kept.
			</p>

			<div class="flex-1"></div>

			<button
				type="button"
				class="h-8 inline-flex items-center justify-center rounded border border-line-strong bg-surface-2 text-xs text-fg-muted hover:border-line-hover hover:text-fg transition-colors"
				on:click={clearMask}
			>
				Clear mask
			</button>
		</div>
	</div>

	<svelte:fragment slot="footer">
		<EditorSaveControls
			applyLabel="Save mask"
			allowReplace={false}
			{busy}
			blockedReason={rejection}
			{failureMessage}
			onCancel={onClose}
			onSave={save}
		/>
	</svelte:fragment>
</EditorShell>
