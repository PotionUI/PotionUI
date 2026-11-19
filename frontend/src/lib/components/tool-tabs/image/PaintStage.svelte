<script lang="ts" module>
	export interface PaintLayerOut {
		file: File;
		name: string;
		opacity: number;
		visible: boolean;
	}

	export interface PaintResult {
		layers: PaintLayerOut[];
		size: { width: number; height: number };
	}
</script>

<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { canvasToPngFile } from '$lib/paint/flatten';
	import { canvasFromUrl } from '$lib/paint/loadPicked';
	import { PaintSession, type SessionSnapshot } from '$lib/paint/session';
	import { Spinner } from '$lib/components/ui';
	import { getFrameContext } from '$lib/tool-tabs/frame/context';
	import { CANVAS_ZOOM_RESERVE, MAX_ZOOM, MIN_ZOOM, ZOOM_STEP } from '$lib/tool-tabs/frame/viewport';
	import CanvasZoomControl from '../frame/CanvasZoomControl.svelte';

	let {
		base = null,
		layerUrl = null,
		layerName = null,
		layerOpacity = 1,
		above = null,
		picture = null,
		tool = 'brush',
		name,
		session = new PaintSession(),
		prepare = null,
		zoomControl = false,
		onstate
	}: {
		base?: HTMLCanvasElement | null;
		layerUrl?: string | null;
		layerName?: string | null;
		layerOpacity?: number;
		above?: HTMLCanvasElement | null;
		picture?: { canvas: HTMLCanvasElement; name: string } | null;
		tool?: string;
		name: string;
		session?: PaintSession;
		prepare?: ((session: PaintSession) => void | Promise<void>) | null;
		zoomControl?: boolean;
		onstate: (session: PaintSession, state: SessionSnapshot) => void;
	} = $props();

	const frame = getFrameContext();
	let host = $state<HTMLDivElement>();
	let stage = $state<HTMLCanvasElement>();
	let overlay = $state<HTMLCanvasElement>();
	let snapshot = $state.raw<SessionSnapshot>(untrack(() => session.snapshot()));
	let original: HTMLCanvasElement | null = null;
	let baseId = -1;
	let targetId = -1;
	let aboveId = -1;
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
			if (prepare) {
				await prepare(session);
			} else if (base) {
				session.openImage(base, name);
				original = session.flatten();
				baseId = session.doc.layers[0]?.id ?? -1;
				if (layerUrl) {
					const stored = await canvasFromUrl(layerUrl);
					const piece = document.createElement('canvas');
					piece.width = session.docWidth;
					piece.height = session.docHeight;
					piece.getContext('2d')?.drawImage(stored, 0, 0, piece.width, piece.height);
					const added = session.addPieceLayer(piece, 0, 0, layerName || 'Paint');
					added.opacity = layerOpacity;
					targetId = added.id;
				} else {
					session.addLayer();
					if (layerName) session.renameLayer(session.doc.activeIndex, layerName);
				}
				if (above) {
					const active = session.doc.activeIndex;
					const overlay = session.addPieceLayer(above, 0, 0, 'Layers above');
					overlay.internal = true;
					aboveId = overlay.id;
					session.setActiveLayer(active);
				}
				session.markSaved();
				session.setTool(tool);
				if (picture) {
					session.addImage(picture.canvas, picture.name);
				}
			} else {
				throw new Error('Nothing to paint on');
			}
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

	export function previewOpacity(value: number): void {
		const layer = session.doc.layers.find((candidate) => candidate.id === targetId);
		if (!layer || layer.opacity === value) return;
		layer.opacity = value;
		session.invalidate();
	}

	export function renameTarget(value: string): void {
		const index = session.doc.layers.findIndex((layer) => layer.id === targetId);
		if (index >= 0) session.renameLayer(index, value);
	}

	export function changed(): boolean {
		return snapshot.dirty;
	}

	function docCanvas(layer: { canvas: HTMLCanvasElement; x: number; y: number }, width: number, height: number): HTMLCanvasElement {
		const out = document.createElement('canvas');
		out.width = width;
		out.height = height;
		out.getContext('2d')?.drawImage(layer.canvas, layer.x, layer.y);
		return out;
	}

	function hasPixels(canvas: HTMLCanvasElement): boolean {
		const data = canvas.getContext('2d')?.getImageData(0, 0, canvas.width, canvas.height).data;
		if (!data) return false;
		for (let i = 3; i < data.length; i += 4) if (data[i] !== 0) return true;
		return false;
	}

	function baseEdits(width: number, height: number): HTMLCanvasElement | null {
		const base = session.doc.layers.find((layer) => layer.id === baseId);
		if (!base || !original) return null;
		const before = original.getContext('2d')?.getImageData(0, 0, width, height);
		const after = docCanvas(base, width, height).getContext('2d')?.getImageData(0, 0, width, height);
		if (!before || !after) return null;
		const out = document.createElement('canvas');
		out.width = width;
		out.height = height;
		const context = out.getContext('2d');
		if (!context) return null;
		const layer = context.createImageData(width, height);
		let painted = false;
		for (let i = 0; i < after.data.length; i += 4) {
			if (after.data[i] === before.data[i] && after.data[i + 1] === before.data[i + 1] && after.data[i + 2] === before.data[i + 2] && after.data[i + 3] === before.data[i + 3]) continue;
			painted = true;
			layer.data.set(after.data.subarray(i, i + 4), i);
		}
		if (!painted) return null;
		context.putImageData(layer, 0, 0);
		return out;
	}

	export async function exportLayers(): Promise<PaintResult | null> {
		if (!snapshot.ready || (targetId >= 0 && !snapshot.dirty)) return null;
		const width = session.docWidth;
		const height = session.docHeight;
		const layers: PaintLayerOut[] = [];
		let count = 0;
		const edited = baseEdits(width, height);
		if (edited) layers.push({ file: await canvasToPngFile(edited, 'paint-layer.png'), name: 'Original edits', opacity: 1, visible: true });
		for (const layer of session.doc.layers) {
			if (layer.id === baseId || layer.id === aboveId) continue;
			const canvas = docCanvas(layer, width, height);
			if (!hasPixels(canvas)) continue;
			count += 1;
			layers.push({ file: await canvasToPngFile(canvas, `paint-layer-${count}.png`), name: layer.name, opacity: layer.opacity, visible: layer.visible });
		}
		return { layers, size: { width, height } };
	}

	function pointerDown(event: PointerEvent) {
		if (document.activeElement instanceof HTMLElement && document.activeElement !== document.body) document.activeElement.blur();
		stage?.setPointerCapture(event.pointerId);
		session.onPointerDown(event);
		event.preventDefault();
	}
</script>

<div bind:this={host} class="relative min-h-0 min-w-0 flex-1 touch-none overflow-hidden bg-scrim" data-paint-stage data-paint-ready={snapshot.ready || undefined}>
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
