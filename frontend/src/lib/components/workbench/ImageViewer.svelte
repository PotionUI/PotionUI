<script lang="ts">
	import { createEventDispatcher } from 'svelte';

	export let imageUrl: string;
	export let comparisonImage: string | null = null;
	export let isComparing: boolean = false;
	export let isZoomMode: boolean = false;

	const dispatch = createEventDispatcher<{
		exitComparison: void;
		exitZoom: void;
		imageClick: MouseEvent;
		imageDoubleClick: MouseEvent;
	}>();

	// Comparison slider state
	let sliderPosition = 50;
	let isDraggingSlider = false;

	// Zoom and Pan state
	let zoomScale = 1.0;
	let panPosition = { x: 0, y: 0 };
	let isDraggingPan = false;
	let dragStart = { x: 0, y: 0, panX: 0, panY: 0 };

	// Reset zoom state when entering/exiting zoom mode
	$: if (isZoomMode) {
		zoomScale = 1.0;
		panPosition = { x: 0, y: 0 };
		isDraggingPan = false;
	}

	// Reset slider when entering comparison mode
	$: if (isComparing) {
		sliderPosition = 50;
		isDraggingSlider = false;
	}

	// ---- Comparison slider handlers ----
	// Pointer Events + pointer capture: the drag tracks 1:1 and keeps following the cursor
	// even when it leaves the image, and it works for touch/pen too.
	let comparisonEl: HTMLDivElement;

	function clampPercent(value: number) {
		return Math.max(0, Math.min(100, value));
	}

	function positionFromClientX(clientX: number) {
		if (!comparisonEl) return sliderPosition;
		const rect = comparisonEl.getBoundingClientRect();
		return clampPercent(((clientX - rect.left) / rect.width) * 100);
	}

	function handleSliderPointerDown(event: PointerEvent) {
		isDraggingSlider = true;
		comparisonEl.setPointerCapture(event.pointerId);
		sliderPosition = positionFromClientX(event.clientX);
	}

	function handleSliderPointerMove(event: PointerEvent) {
		if (!isDraggingSlider) return;
		sliderPosition = positionFromClientX(event.clientX);
	}

	function handleSliderPointerUp(event: PointerEvent) {
		if (!isDraggingSlider) return;
		isDraggingSlider = false;
		try {
			comparisonEl.releasePointerCapture(event.pointerId);
		} catch {
			/* pointer already released */
		}
	}

	function handleSliderKeydown(event: KeyboardEvent) {
		const step = event.shiftKey ? 10 : 2;
		if (event.key === 'ArrowLeft') {
			sliderPosition = clampPercent(sliderPosition - step);
			event.preventDefault();
		} else if (event.key === 'ArrowRight') {
			sliderPosition = clampPercent(sliderPosition + step);
			event.preventDefault();
		} else if (event.key === 'Home') {
			sliderPosition = 0;
			event.preventDefault();
		} else if (event.key === 'End') {
			sliderPosition = 100;
			event.preventDefault();
		}
	}

	// ---- Zoom and Pan handlers ----
	export function handleZoomIn() {
		zoomScale = Math.min(5.0, zoomScale + 0.25);
	}

	export function handleZoomOut() {
		zoomScale = Math.max(0.5, zoomScale - 0.25);
	}

	export function handleResetZoom() {
		zoomScale = 1.0;
		panPosition = { x: 0, y: 0 };
	}

	function handleZoomWheel(event: WheelEvent) {
		event.preventDefault();
		const delta = event.deltaY > 0 ? -0.1 : 0.1;
		zoomScale = Math.max(0.5, Math.min(5.0, zoomScale + delta));
	}

	function handlePanStart(event: MouseEvent) {
		isDraggingPan = true;
		dragStart = {
			x: event.clientX,
			y: event.clientY,
			panX: panPosition.x,
			panY: panPosition.y
		};
	}

	function handlePanMove(event: MouseEvent) {
		if (!isDraggingPan) return;

		const deltaX = event.clientX - dragStart.x;
		const deltaY = event.clientY - dragStart.y;

		panPosition = {
			x: dragStart.panX + deltaX,
			y: dragStart.panY + deltaY
		};
	}

	function handlePanEnd() {
		isDraggingPan = false;
	}
</script>

<!-- Image Comparison overlay with slider -->
{#if isComparing && comparisonImage}
	<div
		bind:this={comparisonEl}
		class="absolute inset-0 z-30 comparison-container cursor-col-resize select-none touch-none"
		on:pointerdown={handleSliderPointerDown}
		on:pointermove={handleSliderPointerMove}
		on:pointerup={handleSliderPointerUp}
		on:pointercancel={handleSliderPointerUp}
		on:keydown={handleSliderKeydown}
		role="slider"
		tabindex="0"
		aria-label="Image comparison position"
		aria-orientation="horizontal"
		aria-valuenow={Math.round(sliderPosition)}
		aria-valuemin={0}
		aria-valuemax={100}
	>
		<!-- Base image (current image) -->
		<div class="absolute inset-0">
			<img
				src={imageUrl}
				alt="Current"
				class="w-full h-full object-contain pointer-events-none"
				draggable="false"
				loading="lazy"
			/>
		</div>

		<!-- Comparison image with clip-path -->
		<div
			class="absolute inset-0"
			style="clip-path: inset(0 {100 - sliderPosition}% 0 0); {isDraggingSlider ? '' : 'transition: clip-path 0.12s ease-out;'}"
		>
			<img
				src={comparisonImage}
				alt="Comparison"
				class="w-full h-full object-contain pointer-events-none"
				draggable="false"
				loading="lazy"
			/>
		</div>

		<!-- Slider line + handle -->
		<div
			class="absolute inset-y-0 w-px bg-white/90 pointer-events-none"
			style="left: {sliderPosition}%; transform: translateX(-50%); box-shadow: 0 0 0 1px rgb(0 0 0 / 0.25); {isDraggingSlider ? '' : 'transition: left 0.12s ease-out;'}"
		>
			<!-- Slider handle (visual; the container owns interaction) -->
			<div
				class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 flex h-9 w-9 items-center justify-center rounded-full bg-white text-canvas shadow-floating transition-transform duration-150 {isDraggingSlider ? 'scale-110' : ''}"
				aria-hidden="true"
			>
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M11 7l-4 5 4 5M13 7l4 5-4 5" />
				</svg>
			</div>
		</div>

		<!-- Exit comparison button -->
		<button
			on:pointerdown|stopPropagation
			on:click|stopPropagation={() => dispatch('exitComparison')}
			class="absolute top-4 right-4 bg-black/50 hover:bg-black/70 text-white p-3 rounded-lg shadow-lg backdrop-blur-sm transition-colors pointer-events-auto"
			title="Exit comparison mode"
		>
			<svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
			</svg>
		</button>
	</div>
{:else if isZoomMode}
	<!-- Zoom and Pan overlay -->
	<div
		class="absolute inset-0 z-30 overflow-hidden bg-surface-1 select-none"
		on:wheel={handleZoomWheel}
		on:mousedown={handlePanStart}
		on:mousemove={handlePanMove}
		on:mouseup={handlePanEnd}
		on:mouseleave={handlePanEnd}
		role="img"
		aria-label="Zoom and pan viewer"
		style="cursor: {isDraggingPan ? 'grabbing' : 'grab'}; user-select: none; -webkit-user-select: none; -moz-user-select: none; -ms-user-select: none;"
	>
		<!-- Zoomed image -->
		<div class="absolute inset-0 flex items-center justify-center select-none">
			<img
				src={imageUrl}
				alt="Zoomed"
				draggable="false"
				class="object-contain pointer-events-none select-none"
				style="transform: scale({zoomScale}) translate({panPosition.x / zoomScale}px, {panPosition.y / zoomScale}px); transform-origin: center center;"
				loading="lazy"
			/>
		</div>

		<!-- Zoom controls - floating on top-right -->
		<div class="absolute top-4 right-4 flex flex-col gap-2 bg-black/70 backdrop-blur-sm rounded-lg p-2 shadow-xl">
			<!-- Zoom percentage display -->
			<div class="text-white text-center text-xs font-mono tabular-nums px-2 py-1">
				{Math.round(zoomScale * 100)}%
			</div>

			<!-- Zoom in button -->
			<button
				on:click|stopPropagation={handleZoomIn}
				class="bg-white/20 hover:bg-white/30 text-white p-2 rounded transition-colors flex items-center justify-center"
				title="Zoom in"
				disabled={zoomScale >= 5.0}
			>
				<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
				</svg>
			</button>

			<!-- Zoom out button -->
			<button
				on:click|stopPropagation={handleZoomOut}
				class="bg-white/20 hover:bg-white/30 text-white p-2 rounded transition-colors flex items-center justify-center"
				title="Zoom out"
				disabled={zoomScale <= 0.5}
			>
				<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18 12H6" />
				</svg>
			</button>

			<!-- Reset zoom button -->
			<button
				on:click|stopPropagation={handleResetZoom}
				class="bg-white/20 hover:bg-white/30 text-white p-2 rounded transition-colors flex items-center justify-center"
				title="Reset zoom (1:1)"
			>
				<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
				</svg>
			</button>

			<div class="h-px bg-white/20 my-1"></div>

			<!-- Exit zoom mode button -->
			<button
				on:click|stopPropagation={() => dispatch('exitZoom')}
				class="bg-white/20 hover:bg-white/30 text-white p-2 rounded transition-colors flex items-center justify-center"
				title="Exit zoom mode"
			>
				<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>
		</div>

		<!-- Hint text at bottom center -->
		<div class="absolute bottom-4 left-1/2 transform -translate-x-1/2 bg-black/70 backdrop-blur-sm text-white px-4 py-2 rounded-full text-sm shadow-lg select-none">
			<div class="flex items-center gap-2">
				<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 15l-2 5L9 9l11 4-5 2zm0 0l5 5M7.188 2.239l.777 2.897M5.136 7.965l-2.898-.777M13.95 4.05l-2.122 2.122m-5.657 5.656l-2.12 2.122" />
				</svg>
				<span>Scroll to zoom • Drag to pan</span>
			</div>
		</div>
	</div>
{:else}
	<!-- Normal image display -->
	{#key imageUrl}
		<img
			src={imageUrl}
			alt="Current generation"
			class="w-full h-full object-contain cursor-pointer"
			on:click={(e) => dispatch('imageClick', e)}
			on:dblclick={(e) => dispatch('imageDoubleClick', e)}
			role="button"
			tabindex="0"
			on:keydown={(e) => e.key === 'Enter' && dispatch('imageClick', new MouseEvent('click'))}
			loading="lazy"
		/>
	{/key}
{/if}
