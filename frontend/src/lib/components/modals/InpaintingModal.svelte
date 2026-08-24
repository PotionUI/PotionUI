<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { toasts } from '$lib/stores/toast';
	import BaseModal from './BaseModal.svelte';
	import { Button } from '$lib/components/ui';

	export let isOpen: boolean = false;
	export let onClose: () => void;
	export let imageUrl: string;
	export let existingMaskUrl: string | null = null;
	export let onSubmit: (maskBase64: string) => Promise<void>;

	let canvasRef: HTMLCanvasElement;
	let canvasContainerRef: HTMLDivElement;
	let imageRef: HTMLImageElement;
	let ctx: CanvasRenderingContext2D | null = null;
	let isDrawing = false;
	let brushSize = 50;
	let maskOpacity = 0.75;
	let isSubmitting = false;
	let isCanvasInitialized = false;

	// Drawing state
	let lastX = 0;
	let lastY = 0;

	// Cursor preview state (relative to canvas container)
	let cursorX = 0;
	let cursorY = 0;
	let showCursor = false;

	// Initialize canvas when modal opens (only once per open)
	$: if (isOpen && canvasRef && !isCanvasInitialized) {
		initializeCanvas();
		isCanvasInitialized = true;
	}

	// Reset canvas context when modal closes
	$: if (!isOpen) {
		ctx = null;
		isCanvasInitialized = false;
	}

	function initializeCanvas() {
		ctx = canvasRef.getContext('2d');
		if (!ctx) {
			logger.error('Failed to get canvas context');
			return;
		}

		// Load image
		const img = new Image();
		img.crossOrigin = 'anonymous';
		img.onload = () => {
			// Set canvas size to match image
			canvasRef.width = img.width;
			canvasRef.height = img.height;

			// Draw image
			ctx!.drawImage(img, 0, 0);

			// Load existing mask if available
			if (existingMaskUrl) {
				const maskImg = new Image();
				maskImg.crossOrigin = 'anonymous';
				maskImg.onload = () => {
					// Create a temporary canvas to convert black/white mask to overlay
					const tempCanvas = document.createElement('canvas');
					tempCanvas.width = canvasRef.width;
					tempCanvas.height = canvasRef.height;
					const tempCtx = tempCanvas.getContext('2d');
					if (tempCtx) {
						tempCtx.drawImage(maskImg, 0, 0);
						const imageData = tempCtx.getImageData(0, 0, tempCanvas.width, tempCanvas.height);

						// Apply mask overlay with current opacity
						for (let i = 0; i < imageData.data.length; i += 4) {
							const brightness = imageData.data[i]; // R channel
							if (brightness > 128) { // White areas in mask
								// Draw white overlay on main canvas
								const x = (i / 4) % tempCanvas.width;
								const y = Math.floor((i / 4) / tempCanvas.width);
								ctx!.fillStyle = `rgba(255, 255, 255, ${maskOpacity})`;
								ctx!.fillRect(x, y, 1, 1);
							}
						}
					}
				};
				maskImg.src = existingMaskUrl;
			}

			// Set up drawing context
			ctx!.strokeStyle = `rgba(255, 255, 255, ${maskOpacity})`;
			ctx!.lineWidth = brushSize;
			ctx!.lineCap = 'round';
			ctx!.lineJoin = 'round';
		};
		img.onerror = (e) => {
			logger.error('Failed to load image:', e);
		};
		img.src = imageUrl;
		imageRef = img;
	}

	function startDrawing(e: MouseEvent) {
		if (!ctx) {
			logger.error('Context not available when starting drawing');
			return;
		}
		isDrawing = true;
		const rect = canvasRef.getBoundingClientRect();
		const scaleX = canvasRef.width / rect.width;
		const scaleY = canvasRef.height / rect.height;
		lastX = (e.clientX - rect.left) * scaleX;
		lastY = (e.clientY - rect.top) * scaleY;
	}

	function draw(e: MouseEvent) {
		if (!ctx || !canvasContainerRef) return;

		const canvasRect = canvasRef.getBoundingClientRect();
		const containerRect = canvasContainerRef.getBoundingClientRect();
		const scaleX = canvasRef.width / canvasRect.width;
		const scaleY = canvasRef.height / canvasRect.height;
		const x = (e.clientX - canvasRect.left) * scaleX;
		const y = (e.clientY - canvasRect.top) * scaleY;

		// Update cursor position (relative to container for proper positioning)
		cursorX = e.clientX - containerRect.left;
		cursorY = e.clientY - containerRect.top;

		if (isDrawing) {
			ctx.beginPath();
			ctx.moveTo(lastX, lastY);
			ctx.lineTo(x, y);
			ctx.stroke();

			lastX = x;
			lastY = y;
		}
	}

	function stopDrawing() {
		isDrawing = false;
	}

	function handleMouseEnter() {
		showCursor = true;
	}

	function handleMouseLeave() {
		showCursor = false;
		stopDrawing();
	}

	function clearCanvas() {
		if (!ctx || !imageRef) return;
		ctx.clearRect(0, 0, canvasRef.width, canvasRef.height);
		ctx.drawImage(imageRef, 0, 0);
	}

	async function handleSubmit() {
		if (!canvasRef || !ctx || !imageRef) {
			logger.error('Canvas not initialized', { canvasRef, ctx, imageRef });
			return;
		}

		isSubmitting = true;
		try {
			// Create a mask-only canvas (white on black)
			const maskCanvas = document.createElement('canvas');
			maskCanvas.width = canvasRef.width;
			maskCanvas.height = canvasRef.height;
			const maskCtx = maskCanvas.getContext('2d');
			if (!maskCtx) {
				logger.error('Failed to get mask canvas context');
				return;
			}

			// Fill with black
			maskCtx.fillStyle = 'black';
			maskCtx.fillRect(0, 0, maskCanvas.width, maskCanvas.height);

			// Draw the mask in white by extracting drawn areas from main canvas
			const imageData = ctx.getImageData(0, 0, canvasRef.width, canvasRef.height);

			// Get original image data
			const tempCanvas = document.createElement('canvas');
			tempCanvas.width = canvasRef.width;
			tempCanvas.height = canvasRef.height;
			const tempCtx = tempCanvas.getContext('2d');
			if (!tempCtx) {
				logger.error('Failed to get temp canvas context');
				return;
			}

			tempCtx.drawImage(imageRef, 0, 0);
			const origData = tempCtx.getImageData(0, 0, canvasRef.width, canvasRef.height);

			// Compare pixels to find drawn areas and create mask
			let hasDrawing = false;
			for (let i = 0; i < imageData.data.length; i += 4) {
				const isDifferent =
					Math.abs(imageData.data[i] - origData.data[i]) > 10 ||
					Math.abs(imageData.data[i + 1] - origData.data[i + 1]) > 10 ||
					Math.abs(imageData.data[i + 2] - origData.data[i + 2]) > 10;

				if (isDifferent) {
					hasDrawing = true;
					// Mark as white in mask
					const x = (i / 4) % canvasRef.width;
					const y = Math.floor((i / 4) / canvasRef.width);
					maskCtx.fillStyle = 'white';
					maskCtx.fillRect(x, y, 1, 1);
				}
			}

			if (!hasDrawing) {
				logger.warn('No mask drawn');
				toasts.error('Please draw a mask before submitting');
				return;
			}

			const maskBase64 = maskCanvas.toDataURL('image/png');

			await onSubmit(maskBase64);

			onClose();
		} catch (error) {
			logger.error('Error in handleSubmit:', error);
		} finally {
			isSubmitting = false;
		}
	}

	// Update brush style when size/opacity changes
	$: if (ctx) {
		ctx.strokeStyle = `rgba(255, 255, 255, ${maskOpacity})`;
		ctx.lineWidth = brushSize;
	}
</script>

<BaseModal
	{isOpen}
	title="Create Inpainting Mask"
	subtitle="Draw over the areas you want to regenerate (white = inpaint)"
	sizeClass="md:max-w-6xl md:w-full"
	on:close={onClose}
>
	<!-- Modal Body -->
	<div class="p-6 h-full">
		<div class="flex gap-6 h-full">
			<!-- Canvas Area -->
			<div
				bind:this={canvasContainerRef}
				class="flex-1 bg-black rounded-lg overflow-hidden flex items-center justify-center relative"
			>
				<canvas
					bind:this={canvasRef}
					class="max-w-full max-h-[600px] cursor-none"
					on:mousedown={startDrawing}
					on:mousemove={draw}
					on:mouseup={stopDrawing}
					on:mouseenter={handleMouseEnter}
					on:mouseleave={handleMouseLeave}
				></canvas>
				<!-- Custom cursor preview -->
				{#if showCursor}
					<div
						class="pointer-events-none absolute rounded-full border-2 border-white shadow-lg"
						style="left: {cursorX}px; top: {cursorY}px; width: {brushSize}px; height: {brushSize}px; transform: translate(-50%, -50%); mix-blend-mode: difference;"
					></div>
				{/if}
			</div>

			<!-- Control Panel -->
			<div class="w-64 space-y-6">
				<!-- Brush Size -->
				<div>
					<label class="block text-sm font-medium text-fg mb-2" for="brush-size-input">
						Brush Size: <span class="font-mono tabular-nums text-signal">{brushSize}px</span>
					</label>
					<input
						id="brush-size-input"
						type="range"
						min="1"
						max="500"
						step="1"
						bind:value={brushSize}
						class="range-input w-full h-2 bg-surface-3 rounded-lg appearance-none cursor-pointer"
					/>
				</div>

				<!-- Mask Opacity -->
				<div>
					<label class="block text-sm font-medium text-fg mb-2" for="mask-opacity-input">
						Mask Opacity: <span class="font-mono tabular-nums text-signal">{Math.round(maskOpacity * 100)}%</span>
					</label>
					<input
						id="mask-opacity-input"
						type="range"
						min="0"
						max="1"
						step="0.1"
						bind:value={maskOpacity}
						class="range-input w-full h-2 bg-surface-3 rounded-lg appearance-none cursor-pointer"
					/>
				</div>

				<!-- Clear Button -->
				<Button variant="secondary" class="w-full" onclick={clearCanvas}>Clear Mask</Button>
			</div>
		</div>
	</div>

	<svelte:fragment slot="footer">
		<div class="p-6 flex items-center justify-end gap-3">
			<Button variant="secondary" disabled={isSubmitting} onclick={onClose}>Cancel</Button>
			<Button variant="primary" loading={isSubmitting} disabled={isSubmitting} onclick={handleSubmit}>
				{isSubmitting ? 'Applying...' : 'Apply Mask'}
			</Button>
		</div>
	</svelte:fragment>
</BaseModal>

<style>
	/* Custom range slider thumb — scoped styles use rgb(var(--token)) per design tokens */
	.range-input::-webkit-slider-thumb {
		appearance: none;
		width: 20px;
		height: 20px;
		background: rgb(var(--signal));
		cursor: pointer;
		border-radius: 50%;
	}

	.range-input::-moz-range-thumb {
		width: 20px;
		height: 20px;
		background: rgb(var(--signal));
		cursor: pointer;
		border-radius: 50%;
		border: none;
	}
</style>
