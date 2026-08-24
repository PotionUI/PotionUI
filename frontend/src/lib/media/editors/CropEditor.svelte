<script lang="ts">
	/**
	 * Crop & frame.
	 *
	 * The stage is sized to the media's own aspect and the rectangle is held in
	 * fractions of it, so nothing here has to convert between screen pixels and
	 * source pixels except at the two edges: the pointer deltas coming in, and
	 * `planToOperations` going out. All of that arithmetic lives in
	 * `cropGeometry.ts` and `imageEditPlan.ts`, where it is asserted.
	 *
	 * Orientation is applied BEFORE the crop, which is why rotating carries the
	 * rectangle round with the frame instead of leaving it over different pixels.
	 */
	import { onMount } from 'svelte';
	import EditorShell from './EditorShell.svelte';
	import EditorSaveControls from './EditorSaveControls.svelte';
	import { EDITOR_ICONS } from './editorIcons';
	import {
		CROP_ASPECTS,
		FULL_CROP,
		fitAspect,
		mirrorCropRect,
		moveCropRect,
		resizeCropRect,
		rotateCropRect,
		turn,
		type CropCorner,
		type CropRect
	} from './cropGeometry';
	import {
		EMPTY_IMAGE_PLAN,
		planDisplaySize,
		planOutputSize,
		planToOperations,
		type ImageEditPlan
	} from './imageEditPlan';
	import { describeRejection } from './editOperations';
	import { loadImage } from './loadImage';
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

	const MAX_STAGE_HEIGHT = 420;
	const OUTPUT_PRESETS = [2048, 1536, 1280, 1024, 768, 512];

	const ORIENTATION_TOOLS = [
		{ key: 'rotate-left', label: 'Rotate left', icon: EDITOR_ICONS.rotateLeft },
		{ key: 'rotate-right', label: 'Rotate right', icon: EDITOR_ICONS.rotateRight },
		{ key: 'flip-h', label: 'Flip horizontal', icon: EDITOR_ICONS.flipHorizontal },
		{ key: 'flip-v', label: 'Flip vertical', icon: EDITOR_ICONS.flipVertical }
	] as const;

	let plan: ImageEditPlan = { ...EMPTY_IMAGE_PLAN };
	let aspectKey = 'free';
	let image: HTMLImageElement | null = null;
	let loadError: string | null = null;
	let stageBoxWidth = 0;

	onMount(async () => {
		try {
			image = await loadImage(source.url);
		} catch {
			loadError = 'This image could not be opened for editing.';
		}
	});

	$: sourceSize = image
		? { width: image.naturalWidth, height: image.naturalHeight }
		: { width: source.width ?? 0, height: source.height ?? 0 };
	$: frame = planDisplaySize(plan, sourceSize);
	$: frameAspect = frame.height > 0 ? frame.width / frame.height : 1;
	$: output = planOutputSize(plan, sourceSize);
	$: lockedRatio = CROP_ASPECTS.find((aspect) => aspect.key === aspectKey)?.ratio ?? null;

	// The stage is the media's exact rectangle, so a fraction of the stage is a
	// fraction of the media and the overlay needs no measurement of its own.
	$: stageHeight = Math.max(
		120,
		Math.min(MAX_STAGE_HEIGHT, (stageBoxWidth || MAX_STAGE_HEIGHT * frameAspect) / (frameAspect || 1))
	);
	$: stageWidth = stageHeight * frameAspect;

	// The image is drawn unrotated and turned by CSS, so its box is the stage
	// transposed back. Right-to-left composition: the rotate runs first, then
	// the mirror - the same order `planToOperations` emits.
	$: imageBoxWidth = plan.rotation === 90 || plan.rotation === 270 ? stageHeight : stageWidth;
	$: imageBoxHeight = plan.rotation === 90 || plan.rotation === 270 ? stageWidth : stageHeight;
	$: imageTransform = `translate(-50%, -50%) scale(${plan.flipHorizontal ? -1 : 1}, ${plan.flipVertical ? -1 : 1}) rotate(${plan.rotation}deg)`;

	$: operations = planToOperations(plan, sourceSize);
	$: rejection = loadError ?? blockedReason ?? describeRejection(operations, 'image');

	function setRect(rect: CropRect) {
		plan = { ...plan, rect };
	}

	function chooseAspect(key: string, ratio: number | null) {
		aspectKey = key;
		if (ratio) setRect(fitAspect(plan.rect, ratio, frameAspect));
	}

	function runOrientation(key: (typeof ORIENTATION_TOOLS)[number]['key']) {
		if (key === 'rotate-left' || key === 'rotate-right') {
			const quarters = key === 'rotate-right' ? 1 : -1;
			plan = {
				...plan,
				rotation: turn(plan.rotation, quarters),
				rect: rotateCropRect(plan.rect, quarters)
			};
			return;
		}
		const axis = key === 'flip-h' ? 'horizontal' : 'vertical';
		plan = {
			...plan,
			flipHorizontal: axis === 'horizontal' ? !plan.flipHorizontal : plan.flipHorizontal,
			flipVertical: axis === 'vertical' ? !plan.flipVertical : plan.flipVertical,
			rect: mirrorCropRect(plan.rect, axis)
		};
	}

	function reset() {
		plan = { ...EMPTY_IMAGE_PLAN, rect: FULL_CROP };
		aspectKey = 'free';
	}

	type DragMode = { kind: 'move' } | { kind: 'resize'; corner: CropCorner };

	function startDrag(event: PointerEvent, mode: DragMode) {
		if (event.button !== 0) return;
		event.preventDefault();
		event.stopPropagation();

		// Both the rectangle and the stage size are frozen for the drag: reading
		// them live would let a reactive stage resize mid-drag move the
		// rectangle the pointer is holding still.
		const startRect = plan.rect;
		const originX = event.clientX;
		const originY = event.clientY;
		const width = stageWidth || 1;
		const height = stageHeight || 1;
		const ratio = lockedRatio;
		const stageAspect = frameAspect;

		const move = (moveEvent: PointerEvent) => {
			const dx = (moveEvent.clientX - originX) / width;
			const dy = (moveEvent.clientY - originY) / height;
			setRect(
				mode.kind === 'move'
					? moveCropRect(startRect, dx, dy)
					: resizeCropRect(startRect, mode.corner, dx, dy, { ratio, stageAspect })
			);
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

	function nudge(dx: number, dy: number) {
		setRect(moveCropRect(plan.rect, dx, dy));
	}

	function handleRectKeydown(event: KeyboardEvent) {
		const step = event.shiftKey ? 0.05 : 0.01;
		const moves: Record<string, [number, number]> = {
			ArrowLeft: [-step, 0],
			ArrowRight: [step, 0],
			ArrowUp: [0, -step],
			ArrowDown: [0, step]
		};
		const delta = moves[event.key];
		if (!delta) return;
		event.preventDefault();
		nudge(delta[0], delta[1]);
	}

	$: croppedSize = planOutputSize({ ...plan, targetLongestSide: null }, sourceSize);
	$: outputPresets = OUTPUT_PRESETS.filter(
		(preset) => preset < Math.max(croppedSize.width, croppedSize.height)
	);

	// Percentages of the stage, so the panels track the rectangle without any
	// pixel arithmetic of their own.
	$: dimPanels = [
		{ key: 'top', style: `left:0; top:0; width:100%; height:${plan.rect.y * 100}%;` },
		{
			key: 'bottom',
			style: `left:0; top:${(plan.rect.y + plan.rect.height) * 100}%; width:100%; bottom:0;`
		},
		{
			key: 'left',
			style: `left:0; top:${plan.rect.y * 100}%; width:${plan.rect.x * 100}%; height:${plan.rect.height * 100}%;`
		},
		{
			key: 'right',
			style: `left:${(plan.rect.x + plan.rect.width) * 100}%; top:${plan.rect.y * 100}%; right:0; height:${plan.rect.height * 100}%;`
		}
	];

	const CORNERS: { corner: CropCorner; position: string; cursor: string }[] = [
		{ corner: 'nw', position: '-top-1.5 -left-1.5', cursor: 'nwse-resize' },
		{ corner: 'ne', position: '-top-1.5 -right-1.5', cursor: 'nesw-resize' },
		{ corner: 'sw', position: '-bottom-1.5 -left-1.5', cursor: 'nesw-resize' },
		{ corner: 'se', position: '-bottom-1.5 -right-1.5', cursor: 'nwse-resize' }
	];

	function save(mode: 'new' | 'replace') {
		commit({ via: 'operations', operations, mode });
	}
</script>

<EditorShell
	title="Crop & frame"
	fileName={source.fileName}
	icon="crop"
	widthClass="md:w-[min(64rem,94vw)]"
	{busy}
	{onClose}
>
	<div class="flex flex-col md:flex-row items-stretch min-h-0">
		<div
			bind:clientWidth={stageBoxWidth}
			class="flex-1 min-w-0 flex items-center justify-center p-4 bg-canvas"
		>
			{#if loadError}
				<p class="py-16 text-sm text-danger">{loadError}</p>
			{:else}
				<div
					class="relative overflow-hidden rounded bg-surface-2"
					style="width: {stageWidth}px; height: {stageHeight}px;"
				>
					<img
						src={source.url}
						alt={source.fileName}
						draggable="false"
						class="absolute left-1/2 top-1/2 max-w-none select-none"
						style="width: {imageBoxWidth}px; height: {imageBoxHeight}px; transform: {imageTransform};"
					/>

					<!-- The discarded part is dimmed, not hidden: the user is judging
					     where the cut falls, which needs to be visible on both sides
					     of it. Four panels around the rectangle rather than one sheet
					     with a hole, so the selection shows the untouched picture. -->
					{#each dimPanels as panel (panel.key)}
						<div class="absolute bg-canvas/65 pointer-events-none" style={panel.style}></div>
					{/each}

					<div
						role="button"
						tabindex="0"
						aria-label="Crop rectangle — drag to move, arrow keys to nudge"
						class="absolute cursor-move ring-1 ring-fg"
						style="left: {plan.rect.x * 100}%; top: {plan.rect.y * 100}%; width: {plan.rect
							.width * 100}%; height: {plan.rect.height * 100}%;"
						on:pointerdown={(event) => startDrag(event, { kind: 'move' })}
						on:keydown={handleRectKeydown}
					>
						<div class="absolute inset-0 pointer-events-none">
							<div class="absolute inset-y-0 left-1/3 w-px bg-fg/20"></div>
							<div class="absolute inset-y-0 left-2/3 w-px bg-fg/20"></div>
							<div class="absolute inset-x-0 top-1/3 h-px bg-fg/20"></div>
							<div class="absolute inset-x-0 top-2/3 h-px bg-fg/20"></div>
						</div>

						{#each CORNERS as handle (handle.corner)}
							<button
								type="button"
								aria-label="Resize from the {handle.corner} corner"
								class="absolute {handle.position} w-3 h-3 rounded-[2px] bg-fg"
								style="cursor: {handle.cursor};"
								on:pointerdown={(event) => startDrag(event, { kind: 'resize', corner: handle.corner })}
							></button>
						{/each}
					</div>
				</div>
			{/if}
		</div>

		<div
			class="w-full md:w-64 shrink-0 flex flex-col gap-4 p-4 border-t md:border-t-0 md:border-l border-line bg-surface-1"
		>
			<div>
				<p class="mb-2 font-mono text-2xs uppercase tracking-[0.08em] text-fg-subtle">Aspect</p>
				<div class="grid grid-cols-3 gap-1.5">
					{#each CROP_ASPECTS as aspect (aspect.key)}
						<button
							type="button"
							class="h-7 rounded font-mono text-2xs tracking-[0.04em] border transition-colors {aspectKey ===
							aspect.key
								? 'border-signal/60 bg-signal/10 text-signal'
								: 'border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg'}"
							on:click={() => chooseAspect(aspect.key, aspect.ratio)}
						>
							{aspect.label}
						</button>
					{/each}
				</div>
			</div>

			<div>
				<p class="mb-2 font-mono text-2xs uppercase tracking-[0.08em] text-fg-subtle">Orientation</p>
				<div class="flex gap-1.5">
					{#each ORIENTATION_TOOLS as tool (tool.key)}
						<button
							type="button"
							title={tool.label}
							aria-label={tool.label}
							class="flex-1 h-8 inline-flex items-center justify-center rounded border border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg transition-colors"
							on:click={() => runOrientation(tool.key)}
						>
							<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d={tool.icon} />
							</svg>
						</button>
					{/each}
				</div>
			</div>

			<div class="h-px bg-line"></div>

			<div class="flex flex-col gap-1.5">
				<div class="flex items-center justify-between">
					<span class="text-xs text-fg-subtle">Source</span>
					<span class="font-mono text-xs tabular-nums text-fg-muted">
						{sourceSize.width || '—'}×{sourceSize.height || '—'}
					</span>
				</div>
				<div class="flex items-center justify-between">
					<span class="text-xs text-fg-subtle">Output</span>
					<span class="font-mono text-xs tabular-nums text-success">
						{output.width}×{output.height}
					</span>
				</div>
			</div>

			{#if outputPresets.length > 0}
				<div>
					<p class="mb-2 font-mono text-2xs uppercase tracking-[0.08em] text-fg-subtle">
						Longest side
					</p>
					<div class="flex flex-wrap gap-1.5">
						<button
							type="button"
							class="h-7 px-2 rounded font-mono text-2xs tabular-nums border transition-colors {plan.targetLongestSide ===
							null
								? 'border-signal/60 bg-signal/10 text-signal'
								: 'border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg'}"
							on:click={() => (plan = { ...plan, targetLongestSide: null })}
						>
							FULL
						</button>
						{#each outputPresets as preset (preset)}
							<button
								type="button"
								class="h-7 px-2 rounded font-mono text-2xs tabular-nums border transition-colors {plan.targetLongestSide ===
								preset
									? 'border-signal/60 bg-signal/10 text-signal'
									: 'border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg'}"
								on:click={() => (plan = { ...plan, targetLongestSide: preset })}
							>
								{preset}
							</button>
						{/each}
					</div>
				</div>
			{/if}

			<div class="flex-1"></div>

			<button
				type="button"
				class="h-8 inline-flex items-center justify-center gap-2 rounded border border-line-strong bg-surface-2 text-xs text-fg-muted hover:border-line-hover hover:text-fg transition-colors"
				on:click={reset}
			>
				Reset
			</button>
			<p class="text-2xs text-fg-subtle">Drag inside the frame to move it, corners to resize.</p>
			{#if resourceNote}
				<p class="text-2xs text-fg-muted">{resourceNote}</p>
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
