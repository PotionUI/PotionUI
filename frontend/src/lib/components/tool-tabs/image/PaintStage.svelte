<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { PaintSession, type SessionSnapshot } from '$lib/paint/session';
	import { Spinner } from '$lib/components/ui';
	import { getFrameContext } from '$lib/tool-tabs/frame/context';
	import { CANVAS_ZOOM_RESERVE, MAX_ZOOM, MIN_ZOOM, ZOOM_STEP } from '$lib/tool-tabs/frame/viewport';
	import CanvasZoomControl from '../frame/CanvasZoomControl.svelte';

	let {
		tool = 'brush',
		session = new PaintSession(),
		prepare = null,
		zoomControl = false,
		locked = false,
		onstate
	}: {
		tool?: string;
		session?: PaintSession;
		prepare?: ((session: PaintSession) => void | Promise<void>) | null;
		zoomControl?: boolean;
		locked?: boolean;
		onstate: (session: PaintSession, state: SessionSnapshot) => void;
	} = $props();

	const frame = getFrameContext();
	let host = $state<HTMLDivElement>();
	let stage = $state<HTMLCanvasElement>();
	let overlay = $state<HTMLCanvasElement>();
	let snapshot = $state.raw<SessionSnapshot>(untrack(() => session.snapshot()));
	let observer: ResizeObserver | null = null;
	let stop: (() => void) | null = null;
	let failed = $state(false);
	let docBox = $derived(
		[snapshot.view.panX, snapshot.view.panY, snapshot.width * snapshot.view.zoom, snapshot.height * snapshot.view.zoom]
			.map((value) => Math.round(value * 100) / 100)
			.join(',')
	);
	let detachWheel: (() => void) | null = null;

	$effect(() => {
		session.setReserve(frame?.canvasReserve ?? 0, zoomControl ? CANVAS_ZOOM_RESERVE : 0);
	});

	onMount(async () => {
		if (!stage || !overlay || !host) return;
		session.attach(stage, overlay);
		stop = session.subscribe(() => {
			snapshot = session.snapshot();
			onstate(session, snapshot);
		});
		if (!prepare) session.setTool(tool);
		observer = new ResizeObserver(() => {
			const box = host!.getBoundingClientRect();
			session.resize(box.width, box.height, window.devicePixelRatio || 1);
		});
		observer.observe(host);
		const wheel = (event: WheelEvent) => session.onWheel(event);
		stage.addEventListener('wheel', wheel, { passive: false });
		detachWheel = () => stage?.removeEventListener('wheel', wheel);
		try {
			if (prepare) await prepare(session);
		} catch {
			failed = true;
		}
		snapshot = session.snapshot();
		onstate(session, snapshot);
	});

	onDestroy(() => {
		observer?.disconnect();
		detachWheel?.();
		stop?.();
		session.detach();
	});

	function pointerDown(event: PointerEvent) {
		if (locked || !snapshot.ready) {
			event.preventDefault();
			return;
		}
		if (document.activeElement instanceof HTMLElement && document.activeElement !== document.body) document.activeElement.blur();
		stage?.setPointerCapture(event.pointerId);
		session.onPointerDown(event);
		event.preventDefault();
	}
</script>

<div bind:this={host} class="relative min-h-0 min-w-0 flex-1 touch-none overflow-hidden bg-scrim" data-paint-stage data-paint-ready={(snapshot.ready && !locked) || undefined}>
	<canvas
		bind:this={stage}
		aria-label="Drawing canvas"
		data-doc-box={docBox}
		class="absolute inset-0 block h-full w-full touch-none"
		style="cursor: {snapshot.panning ? 'grabbing' : snapshot.spaceHeld ? 'grab' : session.cursorStyle}"
		onpointerdown={pointerDown}
		onpointermove={(event) => session.onPointerMove(event)}
		onpointerup={(event) => session.onPointerUp(event)}
		onpointercancel={(event) => session.onPointerCancel(event)}
		onpointerleave={() => session.onPointerLeave()}
	></canvas>
	<canvas bind:this={overlay} aria-hidden="true" class="pointer-events-none absolute inset-0 block h-full w-full"></canvas>
	{#if failed}
		<p class="absolute inset-0 flex items-center justify-center p-6 text-sm text-danger">This picture could not be opened for painting.</p>
	{:else if !snapshot.ready}
		<div class="absolute inset-0 flex items-center justify-center"><Spinner /></div>
	{/if}
	{#if zoomControl && snapshot.ready}
		<CanvasZoomControl
			percent={Math.round(snapshot.zoom * 100)}
			canZoomIn={snapshot.zoom < MAX_ZOOM}
			canZoomOut={snapshot.zoom > MIN_ZOOM}
			fitted={snapshot.fitted}
			actual={!snapshot.fitted && snapshot.zoom === 1}
			onzoomin={() => session.zoomBy(ZOOM_STEP)}
			onzoomout={() => session.zoomBy(1 / ZOOM_STEP)}
			onfit={() => session.fit()}
			onactual={() => session.actualSize()}
		/>
	{/if}
</div>
