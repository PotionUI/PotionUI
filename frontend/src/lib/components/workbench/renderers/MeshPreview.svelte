<script lang="ts">
	/**
	 * `workbench.file` core default for `file_type: "mesh"`. Renders a
	 * generated GLB via `<model-viewer>` (`@google/model-viewer`), dynamically
	 * imported client-side because its module calls `customElements.define`
	 * at import time and touches `HTMLElement`, neither of which exist during
	 * SSR. Unlike ImagePreview/VideoPreview/AudioPreview - each backed by a
	 * dedicated branch in `Workbench.svelte` - mesh has no dedicated branch,
	 * so it resolves through the generic `workbench.file` fallback and
	 * receives the raw `file` object rather than a plain URL prop; see
	 * `resolveMeshUrl` for how the URL is pulled out of it.
	 */
	import { onMount, onDestroy } from 'svelte';
	import { browser } from '$app/environment';
	import { Alert, Spinner, EmptyState, IconButton } from '$lib/components/ui';
	import { logger } from '$lib/utils/logger';
	import { resolveMeshUrl, resolveMeshMetadata, type MeshFileLike } from './meshUrl';

	export let file: MeshFileLike | null | undefined = null;

	type Status = 'empty' | 'loading' | 'ready' | 'error';

	$: meshUrl = resolveMeshUrl(file);
	// The backend deliberately ships no thumbnail for meshes (a real preview
	// needs an offscreen GL context) - vertex/face counts plus the filename
	// are what keep a mesh card from being a content-free grey box while the
	// model itself is still loading, and remain visible once it's ready.
	$: meshMeta = resolveMeshMetadata(file);
	$: hasMeshMeta = meshMeta.vertexCount != null || meshMeta.faceCount != null || !!meshMeta.filename;

	function formatCount(n: number | null): string {
		return n == null ? '—' : n.toLocaleString();
	}

	let status: Status = meshUrl ? 'loading' : 'empty';
	let errorMessage = '';
	let moduleReady = false;
	let moduleFailed = false;
	let viewerEl: HTMLElement | null = null;
	let loadTimeoutId: ReturnType<typeof setTimeout> | null = null;

	// Generous but finite: guards the exact failure mode this component exists
	// to avoid - a <model-viewer> whose `load`/`error` events never fire (a
	// hung fetch, a server that never answers) leaving the spinner up forever.
	// It does NOT protect against a genuinely huge mesh janking the main
	// thread mid-parse; that's a synchronous cost inherent to any WebGL glTF
	// stack (three.js included), not something a JS-level timer can preempt.
	const LOAD_TIMEOUT_MS = 45000;

	onMount(async () => {
		if (!browser) return;
		try {
			await import('@google/model-viewer');
			moduleReady = true;
		} catch (err) {
			logger.error('[MeshPreview] failed to load the 3D viewer module', err);
			moduleFailed = true;
		}
	});

	function clearLoadTimeout() {
		if (loadTimeoutId) {
			clearTimeout(loadTimeoutId);
			loadTimeoutId = null;
		}
	}

	function armLoadTimeout() {
		clearLoadTimeout();
		loadTimeoutId = setTimeout(() => {
			if (status === 'loading') {
				status = 'error';
				errorMessage = 'The model took too long to load.';
			}
		}, LOAD_TIMEOUT_MS);
	}

	// Re-enter "loading" whenever there's a new URL - including switching
	// between generations/gallery items while this renderer stays mounted.
	$: if (meshUrl) {
		status = 'loading';
		errorMessage = '';
	} else {
		status = 'empty';
		clearLoadTimeout();
	}

	$: if (browser && moduleReady && meshUrl && status === 'loading') {
		armLoadTimeout();
	}

	$: if (moduleFailed && meshUrl) {
		status = 'error';
		errorMessage = 'The 3D viewer failed to load.';
	}

	function handleLoad() {
		clearLoadTimeout();
		status = 'ready';
	}

	function handleError(event: CustomEvent<{ type: string; sourceError?: Error }>) {
		clearLoadTimeout();
		status = 'error';
		errorMessage = "This model could not be displayed - the file may be corrupted.";
		logger.error('[MeshPreview] model-viewer reported an error', event?.detail);
	}

	function resetView() {
		const el = viewerEl as
			| (HTMLElement & { cameraOrbit?: string; jumpCameraToGoal?: () => void })
			| null;
		if (!el) return;
		el.cameraOrbit = '0deg 75deg auto';
		el.jumpCameraToGoal?.();
	}

	onDestroy(() => clearLoadTimeout());
</script>

<div class="relative w-full h-full flex items-center justify-center">
	{#if status === 'empty'}
		<EmptyState icon="cube" title="No model" description="This generation has no 3D mesh to display." compact />
	{:else if status === 'error'}
		<div class="max-w-sm px-6">
			<Alert variant="danger" icon title="Couldn't display this model">
				{errorMessage}
			</Alert>
		</div>
	{:else}
		{#if status === 'loading'}
			<div class="absolute inset-0 flex flex-col items-center justify-center gap-3 z-10 pointer-events-none">
				<Spinner size="lg" />
				<span class="text-2xs font-mono uppercase tracking-[0.08em] text-fg-subtle">Loading model…</span>
			</div>
		{/if}

		{#if browser && moduleReady && meshUrl}
			<model-viewer
				bind:this={viewerEl}
				src={meshUrl}
				camera-controls
				shadow-intensity="1"
				class="mesh-viewer w-full h-full"
				style="opacity: {status === 'ready' ? 1 : 0}; pointer-events: {status === 'ready' ? 'auto' : 'none'}"
				on:load={handleLoad}
				on:error={handleError}
			></model-viewer>

			{#if status === 'ready'}
				<div class="absolute bottom-3 right-3 z-10">
					<IconButton icon="refresh" label="Reset view" onclick={resetView} />
				</div>
			{/if}

			{#if hasMeshMeta}
				<div class="absolute bottom-3 left-3 z-10 flex flex-col items-start gap-1.5">
					{#if meshMeta.filename}
						<span class="bg-black/70 backdrop-blur-sm text-white px-3 py-1 rounded-full text-2xs font-mono shadow-lg">
							{meshMeta.filename}
						</span>
					{/if}
					<span class="bg-black/70 backdrop-blur-sm text-white px-3 py-1.5 rounded-full shadow-lg">
						<span class="font-mono text-2xs uppercase tracking-[0.06em] tabular-nums">
							{formatCount(meshMeta.vertexCount)} verts · {formatCount(meshMeta.faceCount)} faces
						</span>
					</span>
				</div>
			{/if}
		{/if}
	{/if}
</div>

<style>
	.mesh-viewer {
		display: block;
		background-color: transparent;
		transition: opacity 150ms var(--ease-out-quart, ease-out);
	}
</style>
