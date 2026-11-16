<script lang="ts">
	import { onMount } from 'svelte';
	import EditorShell from './EditorShell.svelte';
	import EditorSaveControls from './EditorSaveControls.svelte';
	import CropOptions from '$lib/components/tool-tabs/image/CropOptions.svelte';
	import CropOverlay from '$lib/components/tool-tabs/image/CropOverlay.svelte';
	import { Button } from '$lib/components/ui';
	import { CROP_ASPECTS, FULL_CROP, type CropRect } from '$lib/media/cropGeometry';
	import { describeRejection } from '$lib/media/editOperations';
	import { EMPTY_IMAGE_PLAN, planDisplaySize, planOutputSize, planToOperations } from '$lib/media/imageEditPlan';
	import { loadImage } from '$lib/media/loadImage';
	import type { CropParams } from '$lib/tool-tabs/image/steps';
	import type { EditorCommitFn, MediaEditorSource } from './types';

	export let source: MediaEditorSource;
	export let busy: boolean = false;
	export let allowReplace: boolean = true;
	export let blockedReason: string | null = null;
	export let resourceNote: string | null = null;
	export let failureMessage: string | null = null;
	export let onClose: () => void;
	export let commit: EditorCommitFn;

	const MAX_STAGE_HEIGHT = 420;
	const OUTPUT_SIDES = [2048, 1536, 1280, 1024, 768, 512];
	const HINT = 'Drag inside the frame to move it, corners to resize. Arrow keys nudge.';

	let params: CropParams = { plan: { ...EMPTY_IMAGE_PLAN }, aspect: 'free' };
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

	$: plan = params.plan;
	$: sourceSize = image
		? { width: image.naturalWidth, height: image.naturalHeight }
		: { width: source.width ?? 0, height: source.height ?? 0 };
	$: frame = planDisplaySize(plan, sourceSize);
	$: frameAspect = frame.height > 0 ? frame.width / frame.height : 1;
	$: output = planOutputSize(plan, sourceSize);
	$: lockedRatio = CROP_ASPECTS.find((aspect) => aspect.key === params.aspect)?.ratio ?? null;

	$: stageHeight = Math.max(
		120,
		Math.min(MAX_STAGE_HEIGHT, (stageBoxWidth || MAX_STAGE_HEIGHT * frameAspect) / (frameAspect || 1))
	);
	$: stageWidth = stageHeight * frameAspect;
	$: turned = plan.rotation === 90 || plan.rotation === 270;
	$: imageBoxWidth = turned ? stageHeight : stageWidth;
	$: imageBoxHeight = turned ? stageWidth : stageHeight;
	$: imageTransform = `translate(-50%, -50%) scale(${plan.flipHorizontal ? -1 : 1}, ${plan.flipVertical ? -1 : 1}) rotate(${plan.rotation}deg)`;

	$: operations = planToOperations(plan, sourceSize);
	$: rejection = loadError ?? blockedReason ?? describeRejection(operations, 'image');

	function setRect(rect: CropRect) {
		params = { ...params, plan: { ...params.plan, rect } };
	}

	function reset() {
		params = { plan: { ...EMPTY_IMAGE_PLAN, rect: FULL_CROP }, aspect: 'free' };
	}

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
					data-crop-stage
				>
					<img
						src={source.url}
						alt={source.fileName}
						draggable="false"
						class="absolute left-1/2 top-1/2 max-w-none select-none"
						style="width: {imageBoxWidth}px; height: {imageBoxHeight}px; transform: {imageTransform};"
					/>
					<CropOverlay
						rect={plan.rect}
						ratio={lockedRatio}
						stageAspect={frameAspect}
						label="{output.width}×{output.height}"
						onchange={setRect}
					/>
				</div>
			{/if}
		</div>

		<div
			class="w-full md:w-64 shrink-0 flex flex-col gap-4 p-4 border-t md:border-t-0 md:border-l border-line bg-surface-1"
		>
			<CropOptions
				{params}
				input={sourceSize}
				aspects={CROP_ASPECTS}
				outputSides={OUTPUT_SIDES}
				sideLimit={OUTPUT_SIDES.length}
				hint={HINT}
				onchange={(next) => (params = next)}
			/>
			<div class="flex-1"></div>
			<Button variant="secondary" size="sm" onclick={reset}>Reset</Button>
		</div>
	</div>

	<svelte:fragment slot="footer">
		<EditorSaveControls
			{busy}
			{allowReplace}
			note={resourceNote}
			blockedReason={rejection}
			{failureMessage}
			onCancel={onClose}
			onSave={save}
		/>
	</svelte:fragment>
</EditorShell>
