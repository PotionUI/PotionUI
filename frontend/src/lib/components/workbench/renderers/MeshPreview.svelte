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
	 *
	 * This is the ONE mesh viewer both the workbench panel and
	 * `GenerationDetailsModal`'s history view embed - all viewer controls
	 * (wireframe, camera presets, auto-rotate, exposure, screenshot,
	 * animation, material inspector) live here so both surfaces get them for
	 * free. See `glb/modelViewerWireframe.ts` and `glb/glbMaterials.ts` for
	 * why wireframe needs a deep import and the inspector parses the GLB
	 * itself instead of going through model-viewer's public API.
	 */
	import { onMount, onDestroy } from 'svelte';
	import { browser } from '$app/environment';
	import { Alert, Spinner, EmptyState, IconButton } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { logger } from '$lib/utils/logger';
	import { resolveMeshUrl, resolveMeshMetadata, resolveMeshFormat, type MeshFileLike } from './meshUrl';
	import { parseGlbContainer } from './glb/glbContainer';
	import { extractMaterials, extractAnimationNames, revokeMaterialChannelUrls, type MaterialInfo } from './glb/glbMaterials';
	import { prepareWireframeSupport, setWireframe } from './glb/modelViewerWireframe';
	import MaterialInspectorPanel from './MaterialInspectorPanel.svelte';

	export let file: MeshFileLike | null | undefined = null;

	type Status = 'empty' | 'loading' | 'ready' | 'error';

	$: meshUrl = resolveMeshUrl(file);
	// The backend deliberately ships no thumbnail for meshes (a real preview
	// needs an offscreen GL context) - vertex/face counts plus the filename
	// are what keep a mesh card from being a content-free grey box while the
	// model itself is still loading, and remain visible once it's ready.
	$: meshMeta = resolveMeshMetadata(file);
	$: hasMeshMeta = meshMeta.vertexCount != null || meshMeta.faceCount != null || !!meshMeta.filename;
	// The material inspector and animation list only exist for a binary glTF
	// container - a .obj/.ply mesh has neither, so there's nothing to fetch.
	$: meshIsGlb = resolveMeshFormat(file) === 'glb';

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

	// ── Viewer control state ─────────────────────────────────────────────
	// Wireframe resets on every new mesh (the scene graph it touches gets
	// replaced on load, see `handleLoad`); auto-rotate/exposure are sticky
	// across a gallery browsing session since they act on the same
	// `<model-viewer>` DOM node the whole time.
	let wireframeOn = false;
	let wireframeSupported = true;
	let autoRotateOn = false;
	let exposureValue = 1;
	let availableAnimations: string[] = [];
	let currentAnimationName: string | null = null;
	let isAnimationPlaying = false;
	let showInspector = false;
	let materials: MaterialInfo[] = [];
	let materialsLoading = false;
	let materialsLoadedForUrl: string | null = null;

	type OpenPopover = 'camera' | 'exposure' | 'animation' | null;
	let openPopover: OpenPopover = null;
	let cameraTriggerEl: HTMLElement | null = null;
	let cameraMenuEl: HTMLElement | null = null;
	let exposureTriggerEl: HTMLElement | null = null;
	let exposureMenuEl: HTMLElement | null = null;
	let animationTriggerEl: HTMLElement | null = null;
	let animationMenuEl: HTMLElement | null = null;

	function togglePopover(which: Exclude<OpenPopover, null>) {
		openPopover = openPopover === which ? null : which;
	}

	// Registered once (not tied to a popover's open state) so a stale
	// listener never survives a popover that closed some other way - the
	// same idiom ChatInput.svelte uses for its portaled Tools menu.
	function handleOutsidePointerDown(e: PointerEvent) {
		if (!openPopover) return;
		const target = e.target as Node;
		const trigger =
			openPopover === 'camera' ? cameraTriggerEl : openPopover === 'exposure' ? exposureTriggerEl : animationTriggerEl;
		const menu = openPopover === 'camera' ? cameraMenuEl : openPopover === 'exposure' ? exposureMenuEl : animationMenuEl;
		if (!menu?.contains(target) && !trigger?.contains(target)) {
			openPopover = null;
		}
	}

	function handleOutsideKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' && openPopover) openPopover = null;
	}

	onMount(() => {
		document.addEventListener('pointerdown', handleOutsidePointerDown, true);
		document.addEventListener('keydown', handleOutsideKeydown);
		return () => {
			document.removeEventListener('pointerdown', handleOutsidePointerDown, true);
			document.removeEventListener('keydown', handleOutsideKeydown);
		};
	});

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

	// A new mesh invalidates every per-model control's cached state: the
	// wireframe flag would otherwise silently stop applying (new scene
	// objects), and stale material/animation data would describe the
	// previous file. Auto-rotate/exposure deliberately survive - they're
	// viewer preferences, not per-model facts.
	let controlsResetForUrl: string | null = null;
	$: if (meshUrl !== controlsResetForUrl) {
		controlsResetForUrl = meshUrl;
		wireframeOn = false;
		availableAnimations = [];
		currentAnimationName = null;
		isAnimationPlaying = false;
		openPopover = null;
		revokeMaterialChannelUrls(materials);
		materials = [];
		materialsLoadedForUrl = null;
		if (showInspector) loadMaterials();
	}

	async function handleLoad() {
		clearLoadTimeout();
		status = 'ready';

		const el = viewerEl as any;
		if (!el) return;

		wireframeSupported = await prepareWireframeSupport(el);
		if (wireframeOn) setWireframe(el, true);

		availableAnimations = el.availableAnimations ?? [];
		currentAnimationName = availableAnimations[0] ?? null;
		isAnimationPlaying = false;

		if (showInspector) loadMaterials();
	}

	function handleError(event: CustomEvent<{ type: string; sourceError?: Error }>) {
		clearLoadTimeout();
		status = 'error';
		errorMessage = "This model could not be displayed - the file may be corrupted.";
		logger.error('[MeshPreview] model-viewer reported an error', event?.detail);
	}

	function resetView() {
		setCameraPreset('0deg 75deg auto');
	}

	function setCameraPreset(orbit: string) {
		const el = viewerEl as any;
		if (!el) return;
		el.cameraOrbit = orbit;
		el.jumpCameraToGoal?.();
		openPopover = null;
	}

	const CAMERA_PRESETS: Array<{ label: string; orbit: string }> = [
		{ label: 'Default', orbit: '0deg 75deg auto' },
		{ label: 'Front', orbit: '0deg 90deg auto' },
		{ label: 'Back', orbit: '180deg 90deg auto' },
		{ label: 'Left', orbit: '-90deg 90deg auto' },
		{ label: 'Right', orbit: '90deg 90deg auto' },
		{ label: 'Top', orbit: '0deg 0deg auto' }
	];

	function toggleWireframe() {
		const el = viewerEl as any;
		if (!el) return;
		const next = !wireframeOn;
		const applied = setWireframe(el, next);
		if (!applied) {
			wireframeSupported = false;
			return;
		}
		wireframeOn = next;
	}

	function toggleAutoRotate() {
		autoRotateOn = !autoRotateOn;
		const el = viewerEl as any;
		if (el) el.autoRotate = autoRotateOn;
	}

	function handleExposureInput(e: Event) {
		exposureValue = Number((e.currentTarget as HTMLInputElement).value);
		const el = viewerEl as any;
		if (el) el.exposure = exposureValue;
	}

	function toggleAnimationPlayback() {
		const el = viewerEl as any;
		if (!el) return;
		if (isAnimationPlaying) {
			el.pause();
			isAnimationPlaying = false;
		} else {
			if (currentAnimationName) el.animationName = currentAnimationName;
			el.play();
			isAnimationPlaying = true;
		}
	}

	function selectAnimation(name: string) {
		const el = viewerEl as any;
		if (!el) return;
		currentAnimationName = name;
		el.animationName = name;
		el.play();
		isAnimationPlaying = true;
		openPopover = null;
	}

	async function takeScreenshot() {
		const el = viewerEl as any;
		if (!el || typeof el.toBlob !== 'function') return;
		try {
			const blob: Blob = await el.toBlob({ mimeType: 'image/png', idealAspect: true });
			const url = URL.createObjectURL(blob);
			const baseName = (meshMeta.filename || 'mesh').replace(/\.[^./]+$/, '');
			const link = document.createElement('a');
			link.href = url;
			link.download = `${baseName}-view.png`;
			link.click();
			URL.revokeObjectURL(url);
		} catch (err) {
			logger.error('[MeshPreview] screenshot failed', err);
		}
	}

	async function loadMaterials() {
		if (!meshUrl || !meshIsGlb || materialsLoadedForUrl === meshUrl) return;
		materialsLoading = true;
		try {
			const response = await fetch(meshUrl);
			const buffer = await response.arrayBuffer();
			const container = parseGlbContainer(buffer);
			revokeMaterialChannelUrls(materials);
			materials = container ? extractMaterials(container) : [];
			materialsLoadedForUrl = meshUrl;
		} catch (err) {
			logger.error('[MeshPreview] failed to read material data', err);
			materials = [];
		} finally {
			materialsLoading = false;
		}
	}

	function toggleInspector() {
		showInspector = !showInspector;
		if (showInspector) loadMaterials();
	}

	onDestroy(() => {
		clearLoadTimeout();
		revokeMaterialChannelUrls(materials);
	});
</script>

<div class="relative w-full h-full flex items-center justify-center overflow-hidden">
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
				<!-- Viewer toolbar - bottom-right, mirrors the panel-header icon+Tooltip
				     convention even though this isn't a panel header. -->
				<!-- z-30: the workbench panel that hosts this renderer has its own
				     bottom-right hover control (a height slider, z-20) - this must
				     sit above it or a hover reveals both and the slider wins clicks. -->
				<div class="absolute bottom-3 right-3 z-30 flex items-center gap-1 bg-black/50 backdrop-blur-sm rounded-lg p-1 shadow-lg">
					{#if wireframeSupported}
						<Tooltip text={wireframeOn ? 'Hide wireframe' : 'Show wireframe'} position="top" delay={150}>
							<IconButton icon="grid" label="Toggle wireframe" size="sm" active={wireframeOn} onclick={toggleWireframe} />
						</Tooltip>
					{/if}

					<div class="relative" bind:this={cameraTriggerEl}>
						<Tooltip text="Camera view" position="top" delay={150}>
							<IconButton
								icon="cube"
								label="Camera view"
								size="sm"
								active={openPopover === 'camera'}
								ariaExpanded={openPopover === 'camera'}
								onclick={() => togglePopover('camera')}
							/>
						</Tooltip>
						{#if openPopover === 'camera'}
							<div
								bind:this={cameraMenuEl}
								class="absolute bottom-full right-0 mb-2 w-36 bg-surface-1 border border-line rounded-lg shadow-floating py-1 z-30"
							>
								{#each CAMERA_PRESETS as preset (preset.label)}
									<button
										type="button"
										class="w-full text-left px-3 py-1.5 text-xs text-fg hover:bg-surface-3/50 transition-colors"
										on:click={() => setCameraPreset(preset.orbit)}
									>
										{preset.label}
									</button>
								{/each}
							</div>
						{/if}
					</div>

					<Tooltip text={autoRotateOn ? 'Stop auto-rotate' : 'Auto-rotate'} position="top" delay={150}>
						<IconButton icon="globe" label="Toggle auto-rotate" size="sm" active={autoRotateOn} onclick={toggleAutoRotate} />
					</Tooltip>

					<div class="relative" bind:this={exposureTriggerEl}>
						<Tooltip text="Exposure" position="top" delay={150}>
							<IconButton
								icon="adjustments-horizontal"
								label="Exposure"
								size="sm"
								active={openPopover === 'exposure'}
								ariaExpanded={openPopover === 'exposure'}
								onclick={() => togglePopover('exposure')}
							/>
						</Tooltip>
						{#if openPopover === 'exposure'}
							<div
								bind:this={exposureMenuEl}
								class="absolute bottom-full right-0 mb-2 w-44 bg-surface-1 border border-line rounded-lg shadow-floating p-3 z-30"
							>
								<div class="flex items-center justify-between mb-1.5">
									<span class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-muted">Exposure</span>
									<span class="font-mono tabular-nums text-2xs text-fg">{exposureValue.toFixed(1)}</span>
								</div>
								<input
									type="range"
									min="0.1"
									max="3"
									step="0.1"
									value={exposureValue}
									on:input={handleExposureInput}
									class="w-full accent-signal"
									data-testid="mesh-exposure-range"
								/>
							</div>
						{/if}
					</div>

					{#if availableAnimations.length > 0}
						<Tooltip text={isAnimationPlaying ? 'Pause animation' : 'Play animation'} position="top" delay={150}>
							<IconButton
								icon={isAnimationPlaying ? 'pause' : 'play'}
								label={isAnimationPlaying ? 'Pause animation' : 'Play animation'}
								size="sm"
								onclick={toggleAnimationPlayback}
							/>
						</Tooltip>
						{#if availableAnimations.length > 1}
							<div class="relative" bind:this={animationTriggerEl}>
								<Tooltip text="Choose animation" position="top" delay={150}>
									<IconButton
										icon="film"
										label="Choose animation"
										size="sm"
										active={openPopover === 'animation'}
										ariaExpanded={openPopover === 'animation'}
										onclick={() => togglePopover('animation')}
									/>
								</Tooltip>
								{#if openPopover === 'animation'}
									<div
										bind:this={animationMenuEl}
										class="absolute bottom-full right-0 mb-2 w-48 max-h-52 overflow-y-auto bg-surface-1 border border-line rounded-lg shadow-floating py-1 z-30"
									>
										{#each availableAnimations as name (name)}
											<button
												type="button"
												class="w-full text-left px-3 py-1.5 text-xs transition-colors {name === currentAnimationName
													? 'text-signal bg-signal/10'
													: 'text-fg hover:bg-surface-3/50'}"
												on:click={() => selectAnimation(name)}
											>
												{name}
											</button>
										{/each}
									</div>
								{/if}
							</div>
						{/if}
					{/if}

					<Tooltip text="Screenshot" position="top" delay={150}>
						<IconButton icon="photo" label="Screenshot" size="sm" onclick={takeScreenshot} />
					</Tooltip>

					{#if meshIsGlb}
						<Tooltip text={showInspector ? 'Hide materials' : 'Inspect materials'} position="top" delay={150}>
							<IconButton
								icon="layers"
								label="Inspect materials"
								size="sm"
								active={showInspector}
								onclick={toggleInspector}
							/>
						</Tooltip>
					{/if}

					<Tooltip text="Reset view" position="top" delay={150}>
						<IconButton icon="refresh" label="Reset view" size="sm" onclick={resetView} />
					</Tooltip>
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

			{#if showInspector}
				<MaterialInspectorPanel {materials} loading={materialsLoading} onClose={() => (showInspector = false)} />
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
