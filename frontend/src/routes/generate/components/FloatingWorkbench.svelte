<script lang="ts">
	import { onDestroy } from 'svelte';
	import { fade, scale } from 'svelte/transition';
	import portal from '$lib/actions/portal';
	import { IconButton } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import GenerationWorkbenchPane from './GenerationWorkbenchPane.svelte';
	import { resizeFloatingWorkbench, type FloatingWorkbenchBounds } from '$lib/generation/floatingWorkbench';
	import type { Tab } from '$lib/types/tabs';

	// The "W" floating workbench: the same GenerationWorkbenchPane the inline
	// right pane renders, shown as a centered window in the area above the
	// Generation Panel (bottom-[73px]) instead of covering it — the point of
	// FE-179 is to bring results forward while the user keeps hitting
	// Generate. Mounted only while `tab.workbenchFloating` is true, at which
	// point the inline pane renders a placeholder instead — so there is never
	// more than one live Workbench instance for a tab to diverge from. z-30,
	// below the Generation Panel's own drawers (z-40/z-50) so H / the
	// settings drawer still open above it. Not modal and no focus trap: the
	// panel below stays part of the keyboard flow, that is the whole point.
	//
	// The window's size is the workbench's own settings, not something this
	// overlay invents: height comes from `tab.workbenchMaxHeight` (the same
	// value the workbench's own height slider sets — Workbench.svelte) plus
	// the workbench's own controls and this window's header, and width from `tab.workbenchFloatingWidth`
	// (set by GenerationPanels from the inline pane's measured width the
	// first time it opens, and from then on by this window's own resize
	// handles). The workbench renders at its own `workbenchMaxHeight` — this
	// window just wraps it, it does not override it.
	let {
		tab,
		onWorkbenchPrevious,
		onWorkbenchNext,
		onWorkbenchHeightChange,
		onMoveToWorkbench,
		onClose,
		onResizeWidth,
		closeShortcut = undefined
	}: {
		tab: Tab;
		onWorkbenchPrevious: () => void;
		onWorkbenchNext: () => void;
		onWorkbenchHeightChange: (event: CustomEvent<string>) => void;
		onMoveToWorkbench: (event: CustomEvent<{ item: any; index: number }>) => void;
		onClose: () => void;
		onResizeWidth: (width: number) => void;
		closeShortcut?: string;
	} = $props();

	const prefersReducedMotion =
		typeof window !== 'undefined' &&
		typeof window.matchMedia === 'function' &&
		window.matchMedia('(prefers-reduced-motion: reduce)').matches;
	const motionDuration = prefersReducedMotion ? 0 : 150;

	const DEFAULT_WIDTH = 640;
	const MIN_WIDTH = 360;
	const MIN_HEIGHT = 240;
	// The overlay's own padding (p-4/md:p-6) is responsive; this is a soft
	// upper resize bound, not a pixel-perfect layout value, so a fixed
	// estimate is fine.
	const OVERLAY_PADDING = 32;

	let width = $derived(parseInt(tab.workbenchFloatingWidth ?? '', 10) || DEFAULT_WIDTH);
	let bodyHeight = $derived(parseInt(tab.workbenchMaxHeight, 10) || 600);

	let overlayWidth = $state(0);
	let overlayHeight = $state(0);
	let headerHeight = $state(48);
	// The window never scrolls: it takes the workbench's full natural height
	// (stage + its own controls). When that does not fit above the panel, the
	// stage is reduced through the same setting the slider edits until it does.
	let bodyEl: HTMLDivElement | undefined = $state();
	let bodyNaturalHeight = $state(0);
	$effect(() => {
		if (!bodyEl || !overlayHeight) return;
		const available = overlayHeight - OVERLAY_PADDING - headerHeight;
		const excess = bodyNaturalHeight - available;
		if (excess > 0 && bodyHeight > MIN_HEIGHT) {
			const fitted = Math.max(MIN_HEIGHT, bodyHeight - excess);
			if (fitted !== bodyHeight) {
				onWorkbenchHeightChange(new CustomEvent('heightChange', { detail: String(fitted) }));
			}
		}
	});

	function bounds(): FloatingWorkbenchBounds {
		return {
			minWidth: MIN_WIDTH,
			maxWidth: Math.max(MIN_WIDTH, overlayWidth - OVERLAY_PADDING),
			minHeight: MIN_HEIGHT,
			maxHeight: Math.max(MIN_HEIGHT, overlayHeight - OVERLAY_PADDING - headerHeight)
		};
	}

	let resizeAxis: 'width' | 'height' | 'both' | null = null;
	let dragStartX = 0;
	let dragStartY = 0;
	let dragStartWidth = 0;
	let dragStartHeight = 0;

	let capturedHandle: HTMLElement | null = null;
	let capturedPointerId: number | null = null;

	function startResize(axis: 'width' | 'height' | 'both') {
		return (event: PointerEvent) => {
			event.preventDefault();
			// Pointer capture retargets every event of this pointer (including
			// the synthetic `click` UI Events fire at pointerup) to the handle
			// itself, no matter where the cursor ends up — without it, a drag
			// that overshoots the window's edge lands its `pointerup`/`click`
			// on the backdrop behind it, and the backdrop's click-to-close
			// handler closes the window mid-drag.
			capturedHandle = event.currentTarget as HTMLElement;
			capturedPointerId = event.pointerId;
			capturedHandle.setPointerCapture(capturedPointerId);
			resizeAxis = axis;
			dragStartX = event.clientX;
			dragStartY = event.clientY;
			dragStartWidth = width;
			dragStartHeight = bodyHeight;
			document.addEventListener('pointermove', handleResize);
			document.addEventListener('pointerup', stopResize);
			document.addEventListener('pointercancel', stopResize);
			document.body.style.cursor =
				axis === 'width' ? 'ew-resize' : axis === 'height' ? 'ns-resize' : 'nwse-resize';
			document.body.style.userSelect = 'none';
		};
	}

	function handleResize(event: PointerEvent) {
		if (!resizeAxis) return;
		const dx = resizeAxis === 'height' ? 0 : event.clientX - dragStartX;
		const dy = resizeAxis === 'width' ? 0 : event.clientY - dragStartY;
		const next = resizeFloatingWorkbench(
			{ width: dragStartWidth, height: dragStartHeight },
			{ dx, dy },
			bounds()
		);
		if (resizeAxis !== 'height') onResizeWidth(next.width);
		if (resizeAxis !== 'width') {
			onWorkbenchHeightChange(new CustomEvent('heightChange', { detail: String(next.height) }));
		}
	}

	function stopResize() {
		if (!resizeAxis) return;
		resizeAxis = null;
		if (capturedHandle && capturedPointerId !== null && capturedHandle.hasPointerCapture(capturedPointerId)) {
			capturedHandle.releasePointerCapture(capturedPointerId);
		}
		capturedHandle = null;
		capturedPointerId = null;
		document.removeEventListener('pointermove', handleResize);
		document.removeEventListener('pointerup', stopResize);
		document.removeEventListener('pointercancel', stopResize);
		document.body.style.cursor = '';
		document.body.style.userSelect = '';
	}

	onDestroy(stopResize);

	function handleBackdropClick(e: MouseEvent) {
		if (e.target === e.currentTarget) onClose();
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			e.preventDefault();
			onClose();
		}
	}
</script>

<svelte:window on:keydown|capture={handleKeydown} />

<div
	bind:clientWidth={overlayWidth}
	bind:clientHeight={overlayHeight}
	use:portal
	class="fixed inset-x-0 top-0 bottom-[73px] z-30 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 md:p-6"
	role="button"
	tabindex="-1"
	aria-label="Close floating workbench"
	onclick={handleBackdropClick}
	onkeydown={(e) => {
		if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
			e.preventDefault();
			onClose();
		}
	}}
	transition:fade={{ duration: motionDuration }}
>
	<div
		class="relative flex flex-col rounded-xl bg-surface-1 shadow-overlay"
		style="width: {width}px; max-width: 100%; max-height: 100%;"
		role="dialog"
		aria-label="Workbench"
		tabindex="-1"
		onclick={(e) => e.stopPropagation()}
		onkeydown={(e) => e.stopPropagation()}
		transition:scale={{ duration: motionDuration, start: 0.96 }}
	>
		<div
			bind:clientHeight={headerHeight}
			data-testid="floating-workbench-header"
			class="flex flex-shrink-0 items-center justify-between border-b border-line px-4 py-3"
		>
			<span class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Workbench</span>
			<Tooltip text="Close" kbd={closeShortcut} position="left" delay={150}>
				<IconButton icon="close" label="Close floating workbench" onclick={onClose} />
			</Tooltip>
		</div>
		<div bind:this={bodyEl} bind:clientHeight={bodyNaturalHeight} class="p-4">
			<GenerationWorkbenchPane
				{tab}
				{onWorkbenchPrevious}
				{onWorkbenchNext}
				{onWorkbenchHeightChange}
				{onMoveToWorkbench}
			/>
		</div>

		<button
			type="button"
			class="absolute inset-y-3 -right-1 w-2 cursor-ew-resize rounded-full transition-colors hover:bg-line-hover"
			aria-label="Resize workbench width"
			onpointerdown={startResize('width')}
		></button>
		<button
			type="button"
			class="absolute inset-x-3 -bottom-1 h-2 cursor-ns-resize rounded-full transition-colors hover:bg-line-hover"
			aria-label="Resize workbench height"
			onpointerdown={startResize('height')}
		></button>
		<button
			type="button"
			class="absolute -bottom-1 -right-1 h-4 w-4 cursor-nwse-resize rounded-full transition-colors hover:bg-line-hover"
			aria-label="Resize workbench"
			onpointerdown={startResize('both')}
		></button>
	</div>
</div>
