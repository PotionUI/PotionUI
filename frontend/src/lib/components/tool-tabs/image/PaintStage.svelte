<script lang="ts" module>
	export interface PaintResult {
		file: File;
		layers: number;
		size: { width: number; height: number };
	}
</script>

<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { canvasToPngFile } from '$lib/paint/flatten';
	import { canvasFromUrl } from '$lib/paint/loadPicked';
	import { PaintSession, type SessionSnapshot } from '$lib/paint/session';
	import { Spinner } from '$lib/components/ui';

	let {
		base = null,
		layerUrl = null,
		tool = 'brush',
		name,
		session = new PaintSession(),
		prepare = null,
		onstate
	}: {
		base?: HTMLCanvasElement | null;
		layerUrl?: string | null;
		tool?: string;
		name: string;
		session?: PaintSession;
		prepare?: ((session: PaintSession) => void | Promise<void>) | null;
		onstate: (session: PaintSession, state: SessionSnapshot) => void;
	} = $props();

	let host = $state<HTMLDivElement>();
	let stage = $state<HTMLCanvasElement>();
	let overlay = $state<HTMLCanvasElement>();
	let snapshot = $state.raw<SessionSnapshot>(untrack(() => session.snapshot()));
	let original: HTMLCanvasElement | null = null;
	let observer: ResizeObserver | null = null;
	let stop: (() => void) | null = null;
	let failed = $state(false);
	let docBox = $derived(
		[snapshot.view.panX, snapshot.view.panY, snapshot.width * snapshot.view.zoom, snapshot.height * snapshot.view.zoom]
			.map((value) => Math.round(value * 100) / 100)
			.join(',')
	);
	let detachWheel: (() => void) | null = null;

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
				if (layerUrl) {
					const stored = await canvasFromUrl(layerUrl);
					const piece = document.createElement('canvas');
					piece.width = session.docWidth;
					piece.height = session.docHeight;
					piece.getContext('2d')?.drawImage(stored, 0, 0, piece.width, piece.height);
					session.addPieceLayer(piece, 0, 0, 'Paint');
				} else {
					session.addLayer();
				}
				session.markSaved();
				session.setTool(tool);
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

	export function changed(): boolean {
		return snapshot.dirty || (!!layerUrl && snapshot.ready);
	}

	export async function exportLayer(): Promise<PaintResult | null> {
		if (!original || !snapshot.ready) return null;
		const flat = session.flatten();
		const width = flat.width;
		const height = flat.height;
		const before = original.getContext('2d')?.getImageData(0, 0, width, height);
		const after = flat.getContext('2d')?.getImageData(0, 0, width, height);
		if (!before || !after) return null;
		const out = document.createElement('canvas');
		out.width = width;
		out.height = height;
		const context = out.getContext('2d');
		if (!context) return null;
		const layer = context.createImageData(width, height);
		let painted = false;
		for (let i = 0; i < after.data.length; i += 4) {
			const differs =
				after.data[i] !== before.data[i] ||
				after.data[i + 1] !== before.data[i + 1] ||
				after.data[i + 2] !== before.data[i + 2] ||
				after.data[i + 3] !== before.data[i + 3];
			if (!differs) continue;
			painted = true;
			layer.data[i] = after.data[i];
			layer.data[i + 1] = after.data[i + 1];
			layer.data[i + 2] = after.data[i + 2];
			layer.data[i + 3] = after.data[i + 3];
		}
		if (!painted) return null;
		context.putImageData(layer, 0, 0);
		return {
			file: await canvasToPngFile(out, 'paint-layer.png'),
			layers: Math.max(1, snapshot.layers.length - 1),
			size: { width, height }
		};
	}

	function pointerDown(event: PointerEvent) {
		if (document.activeElement instanceof HTMLElement && document.activeElement !== document.body) document.activeElement.blur();
		stage?.setPointerCapture(event.pointerId);
		session.onPointerDown(event);
		event.preventDefault();
	}
</script>

<div bind:this={host} class="relative min-h-0 min-w-0 flex-1 touch-none overflow-hidden bg-scrim" data-paint-stage>
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
</div>
