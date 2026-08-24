<script lang="ts">
	export let artifact: {
		artifact_data: {
			compare_image?: string;
			compare_label?: string;
			to_image?: string;
			to_label?: string;
		};
	};

	// Local per-instance state (previously a Map keyed by pipeName-outputIndex-type
	// in GenerationPanelHistory; each artifact now owns its own component instance).
	let sliderMode: 'slider' | 'separate' = 'separate';
	let isDragging = false;
	let sliderContainer: HTMLElement | null = null;

	function toggleCompareMode() {
		sliderMode = sliderMode === 'slider' ? 'separate' : 'slider';
	}

	function handleSliderMove(event: MouseEvent, containerElement: HTMLElement) {
		const rect = containerElement.getBoundingClientRect();
		const x = event.clientX - rect.left;
		const percentage = (x / rect.width) * 100;
		const clampedPercentage = Math.max(0, Math.min(100, percentage));
		containerElement.style.setProperty('--slider-position', `${clampedPercentage}%`);
	}

	function startDragging(event: MouseEvent, container: HTMLElement) {
		isDragging = true;
		sliderContainer = container;
		handleSliderMove(event, container);
	}

	function stopDragging() {
		isDragging = false;
		sliderContainer = null;
	}

	function onMouseMove(event: MouseEvent) {
		if (isDragging && sliderContainer) {
			handleSliderMove(event, sliderContainer);
		}
	}

	function resolveImageSrc(src: string | undefined): string {
		if (!src) return '';
		if (src.startsWith('/api/')) return `http://localhost:8000${src}`;
		if (src.startsWith('http') || src.startsWith('data:')) return src;
		return `data:image/png;base64,${src}`;
	}

	$: beforeImageSrc = resolveImageSrc(artifact.artifact_data.compare_image);
	$: afterImageSrc = resolveImageSrc(artifact.artifact_data.to_image);
</script>

<svelte:window on:mousemove={onMouseMove} on:mouseup={stopDragging} />

<div class="space-y-3">
	<!-- Header with labels and mode toggle -->
	<div class="flex items-center justify-between gap-3 p-3 bg-surface-2/50 border border-line/60">
		<div class="flex items-center gap-2">
			<div class="p-1 bg-surface-3">
				<svg class="w-3.5 h-3.5 text-fg" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
				</svg>
			</div>
			<span class="text-sm font-semibold text-fg-muted">
				{artifact.artifact_data.compare_label || 'Before'} → {artifact.artifact_data.to_label || 'After'}
			</span>
		</div>
		<button
			class="flex items-center gap-1.5 px-2.5 py-1.5 bg-surface-1 border border-line-strong hover:bg-surface-2 transition-colors text-xs font-medium text-fg-muted"
			on:click={toggleCompareMode}
		>
			{#if sliderMode === 'slider'}
				<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
				</svg>
				Separate
			{:else}
				<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" />
				</svg>
				Slider
			{/if}
		</button>
	</div>

	{#if sliderMode === 'slider'}
		<!-- Slider mode (constrained width) -->
		<div class="max-w-lg mx-auto">
			<div
				class="image-comparison-slider relative overflow-hidden border border-line shadow-md select-none"
				style="--slider-position: 50%;"
				on:mousedown={(e) => startDragging(e, e.currentTarget)}
				role="button"
				tabindex="0"
			>
				<!-- After image (bottom layer) -->
				{#if artifact.artifact_data.to_image}
					<img src={afterImageSrc} alt="After" class="w-full h-auto block" draggable="false" />
				{/if}

				<!-- Before image (top layer, clipped) -->
				{#if artifact.artifact_data.compare_image}
					<div class="absolute inset-0 overflow-hidden" style="clip-path: inset(0 calc(100% - var(--slider-position)) 0 0);">
						<img src={beforeImageSrc} alt="Before" class="w-full h-auto block" draggable="false" />
					</div>
				{/if}

				<!-- Slider handle -->
				<div class="absolute top-0 bottom-0 w-0.5 bg-white shadow-lg cursor-ew-resize" style="left: var(--slider-position);">
					<div class="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 w-8 h-8 bg-white rounded-full shadow-lg border-2 border-line-strong flex items-center justify-center">
						<svg class="w-4 h-4 text-fg-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" />
						</svg>
					</div>
				</div>

				<!-- Labels -->
				<div class="absolute top-2 left-2 px-2 py-1 bg-black/60 backdrop-blur-sm text-white text-xs font-semibold">
					{artifact.artifact_data.compare_label || 'Before'}
				</div>
				<div class="absolute top-2 right-2 px-2 py-1 bg-black/70 backdrop-blur-sm text-white text-xs font-semibold">
					{artifact.artifact_data.to_label || 'After'}
				</div>
			</div>
		</div>
	{:else}
		<!-- Separate mode (original grid) -->
		<div class="grid grid-cols-2 gap-4">
			<div>
				<div class="flex items-center gap-2 mb-2">
					<div class="w-2 h-2 bg-fg-subtle rounded-full"></div>
					<p class="text-sm font-semibold text-fg-muted">{artifact.artifact_data.compare_label || 'Before'}</p>
				</div>
				<div class="relative overflow-hidden border border-line shadow-md hover:shadow-lg transition-shadow duration-200">
					{#if artifact.artifact_data.compare_image}
						<img src={beforeImageSrc} alt="Before" class="w-full h-auto" />
					{/if}
				</div>
			</div>
			<div>
				<div class="flex items-center gap-2 mb-2">
					<div class="w-2 h-2 bg-fg-muted rounded-full"></div>
					<p class="text-sm font-semibold text-fg-muted">{artifact.artifact_data.to_label || 'After'}</p>
				</div>
				<div class="relative overflow-hidden border border-line shadow-md hover:shadow-lg transition-shadow duration-200">
					{#if artifact.artifact_data.to_image}
						<img src={afterImageSrc} alt="After" class="w-full h-auto" />
					{/if}
				</div>
			</div>
		</div>
	{/if}
</div>

<style>
	.image-comparison-slider {
		cursor: ew-resize;
		user-select: none;
	}

	.image-comparison-slider img {
		pointer-events: none;
		user-select: none;
	}
</style>
