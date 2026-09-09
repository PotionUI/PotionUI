<script lang="ts">
	import { onDestroy, tick, untrack } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import ConfirmFooter from '$lib/components/modals/ConfirmFooter.svelte';
	import {
		createConfirmSettlementGate,
		getConfirmKeyboardAction,
		settleIfEligible
	} from '$lib/components/modals/confirmKeyboard';
	import Icon from '$lib/components/Icon.svelte';
	import { IconButton, Input, Spinner, Switch } from '$lib/components/ui';
	import { api } from '$lib/services/api/index';
	import { toasts } from '$lib/stores/toast';
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import type { HistoryToolFile, HistoryToolModalProps } from '$lib/history/tools';
	import type { GenerationHistoryItem } from '$lib/types/history';
	import {
		DEFAULT_STITCH_OPTIONS,
		collectParamKeys,
		computeLayout,
		defaultParamKeys,
		drawStitch,
		paramLinesFor,
		stitchFileName,
		clampZoom,
		fitScale,
		previewScaleFor,
		scrollAfterZoom,
		wheelZoomFactor,
		zoomStep,
		MAX_ZOOM,
		MIN_ZOOM,
		ZOOM_STEP,
		type ParamKeyOption,
		type ParamLine,
		type StitchBackground,
		type StitchItem,
		type StitchLayoutMode,
		type StitchOptions,
		type StitchTileMaxSide
	} from '$lib/history/stitch';

	// No `onDone`: stitching reads the selection and writes nothing, so a
	// finished download closes without asking the page to reload or clear.
	let { context, onClose }: HistoryToolModalProps = $props();

	interface LoadedImage {
		item: StitchItem;
		bitmap: ImageBitmap;
	}

	// Only the first paint, before the pane has been measured, uses these.
	const PREVIEW_FALLBACK_WIDTH = 640;
	const PREVIEW_FALLBACK_HEIGHT = 460;
	const PREVIEW_DEBOUNCE_MS = 120;

	const LAYOUTS: Array<{ id: StitchLayoutMode; label: string }> = [
		{ id: 'row', label: 'Row' },
		{ id: 'column', label: 'Column' },
		{ id: 'grid', label: 'Grid' }
	];
	const TILE_SIDES: Array<{ id: StitchTileMaxSide; label: string }> = [
		{ id: 'original', label: 'Original' },
		{ id: 1024, label: '1024' },
		{ id: 512, label: '512' }
	];
	const BACKGROUNDS: Array<{ id: StitchBackground; label: string }> = [
		{ id: 'dark', label: 'Dark' },
		{ id: 'light', label: 'Light' },
		{ id: 'transparent', label: 'None' }
	];

	let loading = $state(true);
	let loaded = $state<LoadedImage[]>([]);
	let availableKeys = $state<ParamKeyOption[]>([]);
	let options = $state<StitchOptions>({ ...DEFAULT_STITCH_OPTIONS });
	let saving = $state(false);
	let previewCanvas = $state<HTMLCanvasElement | null>(null);
	let previewPane = $state<HTMLElement | null>(null);
	let previewTimer: ReturnType<typeof setTimeout> | null = null;
	// 1 is "fit"; the pane's own size decides what that means in pixels.
	let zoom = $state(1);
	let paneSize = $state({ width: PREVIEW_FALLBACK_WIDTH, height: PREVIEW_FALLBACK_HEIGHT });
	let panning = $state(false);
	let panOrigin: { x: number; y: number; scrollLeft: number; scrollTop: number } | null = null;

	const settlementGate = createConfirmSettlementGate();

	let imageFiles = $derived(context.files.filter((entry) => entry.kind === 'image'));
	let lines = $derived(
		loaded.map((entry) =>
			options.showParams ? paramLinesFor(entry.item, options.paramKeys) : ([] as ParamLine[])
		)
	);
	let layout = $derived(
		computeLayout(
			loaded.map((entry, index) => ({
				width: entry.item.width,
				height: entry.item.height,
				lines: lines[index]?.length ?? 0
			})),
			options
		)
	);
	let columnsDisplay = $derived(String(options.columns));
	let fit = $derived(fitScale({ width: layout.width, height: layout.height }, paneSize));
	let preview = $derived(
		previewScaleFor(fit, zoom, { width: layout.width, height: layout.height })
	);
	let zoomPercent = $derived(Math.round(zoom * 100));
	let canPan = $derived(
		preview.displayWidth > paneSize.width || preview.displayHeight > paneSize.height
	);

	function positivePrompt(generation: GenerationHistoryItem): string {
		const direct = generation.form_data?.prompt;
		if (typeof direct === 'string' && direct.trim()) return direct.trim();
		const segments = (generation.segments ?? [])
			.filter((segment) => segment.channel === 'positive' && !segment.is_disabled && segment.text?.trim())
			.sort((a, b) => a.prompt_index - b.prompt_index || a.segment_index - b.segment_index);
		return segments.map((segment) => segment.text.trim()).join(', ');
	}

	async function loadOne(entry: HistoryToolFile): Promise<LoadedImage> {
		const filename = entry.file.file_path.split('/').pop() || entry.file.file_path;
		const url = api.getGenerationImageURL(entry.generation.id, filename);
		const [media, params] = await Promise.all([
			api.getClient().get(url, { responseType: 'blob' }),
			api
				.getGenerationParams(entry.generation.id, entry.index)
				.catch(() => null)
		]);

		const bitmap = await createImageBitmap(media.data as Blob);
		return {
			bitmap,
			item: {
				id: `${entry.generation.id}:${entry.index}`,
				width: entry.file.width || bitmap.width,
				height: entry.file.height || bitmap.height,
				parameters: params?.success ? (params.data?.parameters ?? {}) : {},
				prompt: positivePrompt(entry.generation)
			}
		};
	}

	async function loadAll(entries: HistoryToolFile[]) {
		const results = await Promise.all(
			entries.map((entry) =>
				loadOne(entry).catch((error) => {
					logger.error('Stitch: failed to load an image', getErrorMessage(error));
					return null;
				})
			)
		);

		const usable = results.filter((result): result is LoadedImage => result !== null);
		const dropped = results.length - usable.length;
		if (dropped > 0) {
			toasts.error(`${dropped} image${dropped === 1 ? '' : 's'} could not be loaded and ${dropped === 1 ? 'was' : 'were'} left out`);
		}

		loaded = usable;
		availableKeys = collectParamKeys(usable.map((entry) => entry.item));
		options = { ...options, paramKeys: defaultParamKeys(availableKeys) };
		loading = false;
	}

	$effect(() => {
		const entries = imageFiles;
		untrack(() => {
			loading = true;
			settlementGate.reset();
			void loadAll(entries);
		});
	});

	// The preview canvas draws the full-size composition through a scale
	// transform, so what is on screen is the same geometry the download uses -
	// and it is redrawn at the zoomed scale rather than blown up by CSS, so the
	// parameter text stays crisp until the backing store hits its cap.
	function renderPreview() {
		const canvas = previewCanvas;
		if (!canvas) return;
		if (preview.renderScale <= 0) {
			canvas.width = 0;
			canvas.height = 0;
			return;
		}

		canvas.width = preview.width;
		canvas.height = preview.height;

		const ctx = canvas.getContext('2d');
		if (!ctx) return;
		ctx.setTransform(preview.renderScale, 0, 0, preview.renderScale, 0, 0);
		ctx.imageSmoothingQuality = 'high';
		drawStitch(
			ctx,
			loaded.map((entry) => entry.bitmap),
			layout,
			lines,
			options
		);
	}

	$effect(() => {
		// Touched so the redraw follows every option, every load and every zoom.
		void [previewCanvas, layout, lines, preview, options.background, options.showParams];
		if (previewTimer) clearTimeout(previewTimer);
		previewTimer = setTimeout(() => untrack(renderPreview), PREVIEW_DEBOUNCE_MS);
	});

	// The pane's size is what "fit" means, so it has to be measured rather than
	// assumed - the modal is responsive and the options column reflows.
	$effect(() => {
		const pane = previewPane;
		if (!pane || typeof ResizeObserver === 'undefined') return;

		const observer = new ResizeObserver((entries) => {
			const box = entries[0]?.contentRect;
			if (!box) return;
			const next = { width: Math.round(box.width), height: Math.round(box.height) };
			if (next.width <= 0 || next.height <= 0) return;
			untrack(() => {
				if (next.width === paneSize.width && next.height === paneSize.height) return;
				paneSize = next;
			});
		});
		observer.observe(pane);
		return () => observer.disconnect();
	});

	/**
	 * Zooms by `factor`, keeping the point under (clientX, clientY) put. The
	 * anchor is measured off the canvas itself, before and after: the canvas is
	 * centred with auto margins, so its offset inside the pane jumps as soon as
	 * it starts overflowing and cannot be predicted from the scales alone.
	 */
	async function zoomAround(factor: number, clientX: number, clientY: number) {
		const before = zoom;
		const after = clampZoom(before * factor);
		if (after === before) return;

		const pane = previewPane;
		const canvas = previewCanvas;
		if (!pane || !canvas) {
			zoom = after;
			return;
		}

		const rectBefore = canvas.getBoundingClientRect();

		zoom = after;
		await tick();

		const rectAfter = canvas.getBoundingClientRect();
		const scroll = { left: pane.scrollLeft, top: pane.scrollTop };

		pane.scrollLeft = scrollAfterZoom({
			cursor: clientX,
			startBefore: rectBefore.left,
			startAfter: rectAfter.left,
			sizeBefore: rectBefore.width,
			sizeAfter: rectAfter.width,
			scroll: scroll.left,
			maxScroll: pane.scrollWidth - pane.clientWidth
		});
		pane.scrollTop = scrollAfterZoom({
			cursor: clientY,
			startBefore: rectBefore.top,
			startAfter: rectAfter.top,
			sizeBefore: rectBefore.height,
			sizeAfter: rectAfter.height,
			scroll: scroll.top,
			maxScroll: pane.scrollHeight - pane.clientHeight
		});
	}

	function zoomByStep(direction: 1 | -1) {
		const pane = previewPane;
		if (!pane) {
			zoom = zoomStep(zoom, direction);
			return;
		}
		const rect = pane.getBoundingClientRect();
		void zoomAround(
			direction > 0 ? ZOOM_STEP : 1 / ZOOM_STEP,
			rect.left + rect.width / 2,
			rect.top + rect.height / 2
		);
	}

	async function resetZoom() {
		zoom = 1;
		await tick();
		if (previewPane) {
			previewPane.scrollLeft = 0;
			previewPane.scrollTop = 0;
		}
	}

	// The wheel zooms, with or without a modifier - the preview is a viewport,
	// not a document. Shift is left to the browser so a trackpad or a wheel can
	// still scroll the pane sideways when the composition is bigger than it.
	function handleWheel(event: WheelEvent) {
		if (event.shiftKey) return;
		event.preventDefault();
		void zoomAround(wheelZoomFactor(event.deltaY, event.deltaMode), event.clientX, event.clientY);
	}

	function handlePointerDown(event: PointerEvent) {
		if (event.button !== 0 || !canPan) return;
		const pane = previewPane;
		if (!pane) return;
		panning = true;
		panOrigin = { x: event.clientX, y: event.clientY, scrollLeft: pane.scrollLeft, scrollTop: pane.scrollTop };
		pane.setPointerCapture(event.pointerId);
	}

	function handlePointerMove(event: PointerEvent) {
		const pane = previewPane;
		if (!panning || !panOrigin || !pane) return;
		pane.scrollLeft = panOrigin.scrollLeft - (event.clientX - panOrigin.x);
		pane.scrollTop = panOrigin.scrollTop - (event.clientY - panOrigin.y);
	}

	function endPan(event: PointerEvent) {
		if (!panning) return;
		panning = false;
		panOrigin = null;
		previewPane?.releasePointerCapture(event.pointerId);
	}

	onDestroy(() => {
		if (previewTimer) clearTimeout(previewTimer);
		for (const entry of loaded) entry.bitmap.close();
	});

	function setColumns(raw: string) {
		const parsed = Number.parseInt(raw, 10);
		if (!Number.isFinite(parsed)) return;
		options = { ...options, columns: Math.min(Math.max(parsed, 1), 12) };
	}

	function setGap(raw: string) {
		const parsed = Number.parseInt(raw, 10);
		if (!Number.isFinite(parsed)) return;
		options = { ...options, gap: Math.min(Math.max(parsed, 0), 256) };
	}

	function toggleKey(key: string) {
		const next = options.paramKeys.includes(key)
			? options.paramKeys.filter((candidate) => candidate !== key)
			: [...options.paramKeys, key];
		options = { ...options, paramKeys: next };
	}

	async function download() {
		saving = true;
		try {
			const canvas = document.createElement('canvas');
			canvas.width = layout.width;
			canvas.height = layout.height;
			const ctx = canvas.getContext('2d');
			if (!ctx) throw new Error('Could not get a 2D canvas context');
			ctx.imageSmoothingQuality = 'high';
			drawStitch(
				ctx,
				loaded.map((entry) => entry.bitmap),
				layout,
				lines,
				options
			);

			const blob = await new Promise<Blob | null>((resolve) =>
				canvas.toBlob(resolve, 'image/png')
			);
			if (!blob) throw new Error('Could not encode the stitched image');

			const url = URL.createObjectURL(blob);
			const anchor = document.createElement('a');
			anchor.href = url;
			anchor.download = stitchFileName(new Date());
			document.body.appendChild(anchor);
			anchor.click();
			anchor.remove();
			URL.revokeObjectURL(url);

			onClose();
		} catch (error) {
			logger.error('Stitch: download failed', getErrorMessage(error));
			toasts.error('Could not build the stitched image');
			settlementGate.reset();
		} finally {
			saving = false;
		}
	}

	function handleCancel() {
		settlementGate.settle(onClose);
	}

	function handleConfirm() {
		settleIfEligible(settlementGate, loaded.length > 0 && !loading && !saving, download);
	}

	function handleKeydown(event: KeyboardEvent) {
		if (saving) return;
		const { action, suppress } = getConfirmKeyboardAction(event);
		if (action === 'cancel') handleCancel();
		else if (action === 'confirm') handleConfirm();
		if (suppress) event.preventDefault();
	}
</script>

<svelte:window on:keydown|capture={handleKeydown} />

<BaseModal
	isOpen={true}
	title="Stitch"
	subtitle="Combine the selected images into one"
	size="xl"
	closeable={!saving}
	handleEscapeKey={false}
	on:close={handleCancel}
>
	<svelte:fragment slot="headerIcon">
		<Icon name="grid" className="h-5 w-5 text-fg-muted" />
	</svelte:fragment>

	<div class="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4 p-4 sm:p-6 md:grid-cols-[minmax(0,1fr)_260px]">
		<div
			class="relative h-[min(60vh,640px)] min-h-0 w-full min-w-0 rounded-lg border border-line bg-surface-2"
		>
			<div
				bind:this={previewPane}
				class="flex h-full w-full min-h-0 min-w-0 overflow-auto p-3 {panning
					? 'cursor-grabbing'
					: canPan
						? 'cursor-grab'
						: ''}"
				onwheel={handleWheel}
				onpointerdown={handlePointerDown}
				onpointermove={handlePointerMove}
				onpointerup={endPan}
				onpointercancel={endPan}
				role="region"
				aria-label="Stitch preview viewport"
			>
				<!-- `m-auto` centres, NOT `justify-content`/`align-items`: a centred
				     flex line cannot be scrolled back past its start edge, which
				     clips the left and top of anything that overflows. Auto margins
				     centre the same way and collapse to 0 once it does. -->
				{#if loading}
					<div class="m-auto flex items-center gap-2 text-fg-muted">
						<Spinner size="sm" />
						<span class="text-sm">Loading images…</span>
					</div>
				{:else if loaded.length === 0}
					<span class="m-auto text-sm text-fg-subtle">No images could be loaded.</span>
				{:else}
					<canvas
						bind:this={previewCanvas}
						class="m-auto block max-w-none shrink-0"
						style:width="{preview.displayWidth}px"
						style:height="{preview.displayHeight}px"
						aria-label="Stitch preview"
					></canvas>
				{/if}
			</div>

			{#if !loading && loaded.length > 0}
				<div
					class="absolute right-2 top-2 flex items-center gap-0.5 rounded border border-line bg-surface-1/90 px-1 py-0.5 shadow-raised"
				>
					<IconButton
						icon="minus"
						label="Zoom out"
						size="sm"
						disabled={zoom <= MIN_ZOOM}
						onclick={() => zoomByStep(-1)}
					/>
					<span class="min-w-12 text-center font-mono text-2xs tabular-nums text-fg-muted">
						{zoomPercent}%
					</span>
					<IconButton
						icon="plus"
						label="Zoom in"
						size="sm"
						disabled={zoom >= MAX_ZOOM}
						onclick={() => zoomByStep(1)}
					/>
					<IconButton
						icon="photo"
						label="Fit to view"
						size="sm"
						disabled={zoom === 1}
						onclick={resetZoom}
					/>
				</div>
			{/if}
		</div>

		<div class="min-w-0 space-y-4">
			<p class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle tabular-nums">
				{loaded.length} image{loaded.length === 1 ? '' : 's'}
			</p>

			<div>
				<span class="mb-1.5 block text-sm font-medium text-fg">Layout</span>
				<div class="inline-flex items-center gap-1">
					{#each LAYOUTS as option (option.id)}
						<button
							type="button"
							class="rounded px-2.5 py-1 text-sm transition-colors {options.layout === option.id
								? 'bg-signal/10 text-signal'
								: 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
							aria-pressed={options.layout === option.id}
							onclick={() => (options = { ...options, layout: option.id })}
						>
							{option.label}
						</button>
					{/each}
				</div>
			</div>

			{#if options.layout === 'grid'}
				<div>
					<label class="mb-1.5 block text-sm font-medium text-fg" for="stitch-columns">Columns</label>
					<Input
						id="stitch-columns"
						type="number"
						min="1"
						max="12"
						class="font-mono tabular-nums"
						value={columnsDisplay}
						oninput={(event: Event) => setColumns((event.currentTarget as HTMLInputElement).value)}
					/>
				</div>
			{/if}

			<div>
				<span class="mb-1.5 block text-sm font-medium text-fg">Tile size</span>
				<div class="inline-flex items-center gap-1">
					{#each TILE_SIDES as option (option.label)}
						<button
							type="button"
							class="rounded px-2.5 py-1 font-mono text-sm tabular-nums transition-colors {options.tileMaxSide ===
							option.id
								? 'bg-signal/10 text-signal'
								: 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
							aria-pressed={options.tileMaxSide === option.id}
							onclick={() => (options = { ...options, tileMaxSide: option.id })}
						>
							{option.label}
						</button>
					{/each}
				</div>
			</div>

			<div>
				<label class="mb-1.5 block text-sm font-medium text-fg" for="stitch-gap">Gap</label>
				<Input
					id="stitch-gap"
					type="number"
					min="0"
					max="256"
					class="font-mono tabular-nums"
					value={String(options.gap)}
					oninput={(event: Event) => setGap((event.currentTarget as HTMLInputElement).value)}
				/>
			</div>

			<div>
				<span class="mb-1.5 block text-sm font-medium text-fg">Background</span>
				<div class="inline-flex items-center gap-1">
					{#each BACKGROUNDS as option (option.id)}
						<button
							type="button"
							class="rounded px-2.5 py-1 text-sm transition-colors {options.background === option.id
								? 'bg-signal/10 text-signal'
								: 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
							aria-pressed={options.background === option.id}
							onclick={() => (options = { ...options, background: option.id })}
						>
							{option.label}
						</button>
					{/each}
				</div>
			</div>

			<div class="border-t border-line pt-4">
				<Switch
					label="Show parameters"
					checked={options.showParams}
					onchange={(checked) => (options = { ...options, showParams: checked })}
				/>

				{#if options.showParams}
					{#if availableKeys.length === 0}
						<p class="mt-2 text-xs text-fg-subtle">
							These images carry no parameters to show.
						</p>
					{:else}
						<div class="mt-2 max-h-48 space-y-1 overflow-y-auto pr-1">
							{#each availableKeys as option (option.key)}
								<label class="flex cursor-pointer items-center gap-2 text-xs text-fg-muted">
									<input
										type="checkbox"
										class="accent-accent"
										checked={options.paramKeys.includes(option.key)}
										onchange={() => toggleKey(option.key)}
									/>
									<span class="font-mono tabular-nums">{option.label}</span>
									{#if option.synthetic}
										<span class="text-2xs text-fg-subtle">derived</span>
									{/if}
								</label>
							{/each}
						</div>
					{/if}
				{/if}
			</div>

			{#if !loading && loaded.length > 0}
				<p class="font-mono text-2xs text-fg-subtle tabular-nums">
					{layout.width} × {layout.height}
				</p>
				{#if layout.clamped}
					<p class="text-xs text-warning">
						Too large for one canvas — tiles were reduced to {layout.tileMaxSide} px.
					</p>
				{/if}
			{/if}
		</div>
	</div>

	<svelte:fragment slot="footer">
		<ConfirmFooter
			confirmLabel="Download PNG"
			busy={saving}
			confirmDisabled={loading || loaded.length === 0}
			onCancel={handleCancel}
			onConfirm={handleConfirm}
		/>
	</svelte:fragment>
</BaseModal>
