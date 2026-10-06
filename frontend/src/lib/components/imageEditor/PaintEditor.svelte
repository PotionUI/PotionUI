<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import EditorShell from '$lib/media/editors/EditorShell.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button, Kbd } from '$lib/components/ui';
	import { resumeKeyboard, suppressKeyboard } from '$lib/services/keyboard';
	import type { EditorCommitFn, MediaEditorSource } from '$lib/media/editors/types';
	import CanvasPanel from './CanvasPanel.svelte';
	import type { DrawConfig } from './drawConfig';
	import { defaultExportStem, exportFileName } from './exportName';
	import { PAINT_ICONS } from './icons';
	import FilterStrip from './FilterStrip.svelte';
	import ImageSourceMenu from './ImageSourceMenu.svelte';
	import LayersPanel from './LayersPanel.svelte';
	import { decideMaskFate } from './maskPolicy';
	import OptionsPanel from './OptionsPanel.svelte';
	import SaveFilterDialog from './SaveFilterDialog.svelte';
	import SavePopover from './SavePopover.svelte';
	import { PaintSession, type SessionSnapshot } from './session';
	import ToolButton from './ToolButton.svelte';
	import ToolPanel from './ToolPanel.svelte';
	import type { PickedImage } from './types';

	export let source: MediaEditorSource | null = null;
	export let draw: DrawConfig | null = null;
	export let hasMask: boolean = false;
	export let busy: boolean = false;
	export let failureMessage: string | null = null;
	export let onClose: () => void;
	export let commit: EditorCommitFn;

	type Sheet = 'tool' | 'layers' | 'canvas' | null;

	const session = new PaintSession();

	let stageHost: HTMLDivElement;
	let stageCanvas: HTMLCanvasElement;
	let state: SessionSnapshot = session.snapshot();
	let loadError: string | null = null;
	let panelHidden = false;
	let fullScreen = false;
	let saveOpen = false;
	let discardOpen = false;
	let saveFilterOpen = false;
	let phone = false;
	let phoneQuery: MediaQueryList | null = null;
	let menuMode: 'open' | 'layer' | null = null;
	let sheet: Sheet = null;
	let stem = defaultExportStem(draw ? null : (source?.fileName ?? null));
	let popover: SavePopover | undefined;
	let observer: ResizeObserver | null = null;
	let themeObserver: MutationObserver | null = null;
	let stopSession: (() => void) | null = null;

	function syncPhone() {
		phone = phoneQuery?.matches ?? false;
	}

	$: tool = state.tools.find((candidate) => candidate.id === state.toolId);
	$: decision = decideMaskFate({
		hasMask,
		geometryChanged: state.geometryChanged,
		sourceReplaced: state.sourceReplaced
	});
	$: sizeClass = fullScreen
		? 'md:!w-screen md:!h-screen md:!max-h-none md:!rounded-none'
		: 'md:w-[calc((min(80rem,96vw)_+_100vw_-_32px)/2)] md:h-[calc((min(52rem,92vh)_+_100vh_-_32px)/2)]';
	$: docBox = [
		state.view.panX,
		state.view.panY,
		state.width * state.view.zoom,
		state.height * state.view.zoom
	]
		.map((value) => Math.round(value * 100) / 100)
		.join(',');
	$: zoomLabel = `${Math.round(state.zoom * 100)}%`;
	$: title = draw ? 'Draw' : 'Edit image';
	$: fileName = state.sourceName ?? source?.fileName ?? 'Untitled drawing';
	$: lineageName = draw && !state.sourceReplaced ? null : (state.sourceName ?? source?.fileName ?? null);
	$: cursorStyle = state.panning ? 'grabbing' : state.spaceHeld ? 'grab' : session.cursorStyle;
	$: toolHasOptions = (tool?.options?.length ?? 0) > 0;
	$: sheetTitle = sheet === 'layers' ? 'Layers' : sheet === 'canvas' ? 'Canvas' : (tool?.label ?? 'Options');

	onMount(async () => {
		suppressKeyboard();
		if (typeof matchMedia === 'function') {
			phoneQuery = matchMedia('(max-width: 767px)');
			syncPhone();
			phoneQuery.addEventListener?.('change', syncPhone);
		}
		session.attach(stageCanvas);
		stopSession = session.subscribe(() => {
			state = session.snapshot();
		});

		observer = new ResizeObserver(() => {
			const box = stageHost.getBoundingClientRect();
			session.resize(box.width, box.height, window.devicePixelRatio || 1);
		});
		observer.observe(stageHost);

		themeObserver = new MutationObserver(() => session.refreshTheme());
		themeObserver.observe(document.documentElement, {
			attributes: true,
			attributeFilter: ['data-theme']
		});

		try {
			if (draw) {
				session.openBlank({
					width: draw.width,
					height: draw.height,
					background: draw.background,
					pen: draw.pen
				});
				if (draw.scribble) session.setSetting('size', 6);
			} else if (source) {
				await session.openSource(source.url, source.fileName);
			}
		} catch {
			loadError = 'This image could not be opened for editing.';
		}
		state = session.snapshot();
	});

	onDestroy(() => {
		resumeKeyboard();
		phoneQuery?.removeEventListener?.('change', syncPhone);
		observer?.disconnect();
		themeObserver?.disconnect();
		stopSession?.();
		session.detach();
	});

	function closeSheet() {
		sheet = null;
	}

	function selectTool(id: string) {
		session.setTool(id);
		const next = session.getTool(id);
		const needsSheet = next && next.panel && next.panel !== 'options' && next.panel !== 'selection';
		sheet = needsSheet ? 'tool' : null;
	}

	function toggleSheet(next: Exclude<Sheet, null>) {
		sheet = sheet === next ? null : next;
	}

	function requestClose() {
		if (busy) return;
		if (saveFilterOpen) {
			saveFilterOpen = false;
			return;
		}
		if (menuMode) {
			menuMode = null;
			return;
		}
		if (saveOpen) {
			saveOpen = false;
			return;
		}
		if (discardOpen) {
			discardOpen = false;
			return;
		}
		if (state.filter.active) {
			session.clearFilter();
			return;
		}
		if (state.cropRect) {
			session.cancelCrop();
			return;
		}
		if (state.hasSelection) {
			session.deselect();
			return;
		}
		if (state.dirty) {
			discardOpen = true;
			return;
		}
		onClose();
	}

	function openSave() {
		if (!state.ready || busy) return;
		discardOpen = false;
		menuMode = null;
		saveOpen = true;
		setTimeout(() => popover?.focusName(), 0);
	}

	async function save() {
		if (busy || !state.ready) return;
		const file = await session.exportFile(exportFileName(stem));
		await commit({
			via: 'paint',
			file,
			geometryChanged: session.geometryChanged,
			sourceReplaced: session.sourceReplaced
		});
	}

	function openMenu(mode: 'open' | 'layer') {
		saveOpen = false;
		discardOpen = false;
		menuMode = menuMode === mode ? null : mode;
	}

	function picked(image: PickedImage) {
		if (menuMode === 'open') session.openImage(image.canvas, image.name);
		else session.addImage(image.canvas, image.name);
		menuMode = null;
		sheet = null;
	}

	function isTyping(target: EventTarget | null): boolean {
		if (!(target instanceof HTMLElement)) return false;
		if (target.isContentEditable) return true;
		if (target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return true;
		if (target instanceof HTMLInputElement) {
			return !['range', 'color', 'checkbox', 'radio', 'button'].includes(target.type);
		}
		return false;
	}

	function onKeydown(event: KeyboardEvent) {
		if (isTyping(event.target)) return;
		const key = event.key.toLowerCase();
		const mod = event.ctrlKey || event.metaKey;

		if (key === ' ' && !mod) {
			if (event.target instanceof HTMLButtonElement) return;
			event.preventDefault();
			session.setSpaceHeld(true);
			return;
		}
		if (mod) {
			if (key === 'z') {
				event.preventDefault();
				if (event.shiftKey) session.redo();
				else session.undo();
			} else if (key === 'y') {
				event.preventDefault();
				session.redo();
			} else if (key === 's') {
				event.preventDefault();
				openSave();
			} else if (key === 'c') {
				event.preventDefault();
				session.copySelection();
			} else if (key === 'x') {
				event.preventDefault();
				session.cutSelection();
			} else if (key === 'v') {
				event.preventDefault();
				session.pasteClipboard();
			} else if (key === 'a') {
				event.preventDefault();
				session.selectAll();
			} else if (key === 'd') {
				event.preventDefault();
				session.deselect();
			}
			return;
		}
		if (event.altKey) return;

		if (event.key === '\\' && !mod) {
			if (state.filter.active) {
				event.preventDefault();
				session.setFilterCompare(true);
			}
			return;
		}

		if (key === 'enter') {
			if (state.cropRect) {
				event.preventDefault();
				session.applyCrop();
			}
			return;
		}
		if (key === 'delete' || key === 'backspace') {
			if (state.hasSelection) {
				event.preventDefault();
				session.deleteSelection();
			}
			return;
		}
		if (key.startsWith('arrow') && state.toolId === 'move') {
			event.preventDefault();
			const step = event.shiftKey ? 10 : 1;
			const dx = key === 'arrowleft' ? -step : key === 'arrowright' ? step : 0;
			const dy = key === 'arrowup' ? -step : key === 'arrowdown' ? step : 0;
			session.nudge(dx, dy);
			return;
		}
		if (key === '[') {
			session.adjustSize(-(state.settings.size > 20 ? 5 : 1));
		} else if (key === ']') {
			session.adjustSize(state.settings.size >= 20 ? 5 : 1);
		} else if (key === '+' || key === '=') {
			session.zoomBy(1.25);
		} else if (key === '-') {
			session.zoomBy(1 / 1.25);
		} else if (key === '0') {
			session.fit();
		} else if (key === 'p') {
			panelHidden = !panelHidden;
		} else if (key === 'f') {
			fullScreen = !fullScreen;
		} else {
			const match = state.tools.find((candidate) => candidate.key?.toLowerCase() === key);
			if (match) selectTool(match.id);
		}
	}

	function onKeyup(event: KeyboardEvent) {
		if (event.key === ' ') session.setSpaceHeld(false);
		if (event.key === '\\') session.setFilterCompare(false);
	}

	function pointerDown(event: PointerEvent) {
		stageCanvas.setPointerCapture(event.pointerId);
		session.onPointerDown(event);
		event.preventDefault();
	}

	const topButton =
		'inline-flex items-center justify-center gap-1.5 h-8 px-2 max-md:w-10 max-md:h-10 max-md:px-0 rounded border border-line-strong bg-surface-3 text-xs text-fg transition-colors hover:border-line-hover touch-manipulation';
</script>

<svelte:window on:keydown|capture={onKeydown} on:keyup|capture={onKeyup} />

<EditorShell
	{title}
	{fileName}
	icon="brush"
	widthClass={sizeClass}
	{busy}
	onClose={requestClose}
>
	<div class="flex flex-col md:flex-row h-full min-h-0 bg-canvas">
		<div
			class="order-3 md:order-1 shrink-0 flex flex-row md:flex-col gap-0.5 p-1.5 border-t md:border-t-0 md:border-r border-line bg-surface-1 overflow-x-auto md:overflow-y-auto"
			role="toolbar"
			aria-label="Tools"
		>
			{#each state.tools as candidate, index (candidate.id)}
				{#if index > 0 && state.tools[index - 1].group !== candidate.group}
					<div class="shrink-0 w-px md:w-auto md:h-px mx-0.5 md:mx-0.5 md:my-1 bg-line self-stretch md:self-auto"></div>
				{/if}
				<ToolButton
					icon={candidate.icon}
					label={candidate.label}
					kbd={candidate.key}
					caption={candidate.caption ?? candidate.label}
					position="right"
					pressed={candidate.id === state.toolId}
					onClick={() => selectTool(candidate.id)}
				/>
			{/each}
			<div class="md:hidden shrink-0 w-px mx-0.5 bg-line self-stretch"></div>
			<div class="md:hidden contents">
				<ToolButton
					icon={PAINT_ICONS.layers}
					label="Layers"
					caption="Layers"
					pressed={sheet === 'layers'}
					onClick={() => toggleSheet('layers')}
				/>
				<ToolButton
					icon={PAINT_ICONS.canvas}
					label="Canvas"
					caption="Canvas"
					pressed={sheet === 'canvas'}
					onClick={() => toggleSheet('canvas')}
				/>
			</div>
		</div>

		<div class="order-1 md:order-2 flex flex-col flex-1 min-w-0 min-h-0">
			<div class="relative shrink-0 flex items-center gap-1 px-2 py-1 border-b border-line bg-surface-1">
				<ToolButton
					icon={PAINT_ICONS.undo}
					label={state.undoLabel ? `Undo ${state.undoLabel}` : 'Undo'}
					kbd="Ctrl+Z"
					compact
					disabled={!state.canUndo}
					onClick={() => session.undo()}
				/>
				<ToolButton
					icon={PAINT_ICONS.redo}
					label={state.redoLabel ? `Redo ${state.redoLabel}` : 'Redo'}
					kbd="Ctrl+Y"
					compact
					disabled={!state.canRedo}
					onClick={() => session.redo()}
				/>
				<div class="w-px h-5 mx-1 bg-line shrink-0"></div>
				<Tooltip text="Open another image" position="bottom" wrapperClass="inline-flex shrink-0">
					<button
						type="button"
						data-image-source-trigger
						class={topButton}
						aria-label="Open another image"
						aria-expanded={menuMode === 'open'}
						on:click={() => openMenu('open')}
					>
						<svg class="w-3.5 h-3.5 max-md:w-5 max-md:h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d={PAINT_ICONS.open} /></svg>
						<span class="max-md:hidden">Open</span>
					</button>
				</Tooltip>
				<Tooltip text="Add an image as a new layer" position="bottom" wrapperClass="inline-flex shrink-0">
					<button
						type="button"
						data-image-source-trigger
						class={topButton}
						aria-label="Add image as layer"
						aria-expanded={menuMode === 'layer'}
						on:click={() => openMenu('layer')}
					>
						<svg class="w-3.5 h-3.5 max-md:w-5 max-md:h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d={PAINT_ICONS.addImage} /></svg>
						<span class="max-md:hidden">Add image</span>
					</button>
				</Tooltip>
				<div class="flex-1"></div>
				<ToolButton icon={PAINT_ICONS.minus} label="Zoom out" kbd="-" compact onClick={() => session.zoomBy(1 / 1.25)} />
				<span class="w-12 text-center font-mono text-xs tabular-nums text-fg-muted">{zoomLabel}</span>
				<ToolButton icon={PAINT_ICONS.plus} label="Zoom in" kbd="+" compact onClick={() => session.zoomBy(1.25)} />
				<ToolButton icon={PAINT_ICONS.fit} label="Fit to view" kbd="0" compact onClick={() => session.fit()} />
				<div class="hidden md:flex items-center gap-1 ml-1 pl-2 border-l border-line">
					<ToolButton
						icon={PAINT_ICONS.panel}
						label={panelHidden ? 'Show panel' : 'Hide panel'}
						kbd="P"
						compact
						pressed={panelHidden}
						onClick={() => (panelHidden = !panelHidden)}
					/>
					<ToolButton
						icon={PAINT_ICONS.full}
						label={fullScreen ? 'Exit full screen' : 'Full screen'}
						kbd="F"
						compact
						pressed={fullScreen}
						onClick={() => (fullScreen = !fullScreen)}
					/>
				</div>

				{#if menuMode}
					<ImageSourceMenu
						mode={menuMode}
						dirty={state.dirty}
						onPick={picked}
						onClose={() => (menuMode = null)}
					/>
				{/if}
			</div>

			<div bind:this={stageHost} class="relative flex-1 min-h-0 overflow-hidden bg-canvas touch-none">
				<canvas
					bind:this={stageCanvas}
					aria-label="Drawing canvas"
					data-doc-box={docBox}
					class="absolute inset-0 block w-full h-full touch-none"
					style="cursor: {cursorStyle}"
					on:pointerdown={pointerDown}
					on:pointermove={(event) => session.onPointerMove(event)}
					on:pointerup={(event) => session.onPointerUp(event)}
					on:pointercancel={(event) => session.onPointerCancel(event)}
					on:pointerleave={() => session.onPointerLeave()}
					on:wheel|nonpassive={(event) => session.onWheel(event)}
				></canvas>
				{#if loadError}
					<p class="absolute inset-0 flex items-center justify-center p-6 text-sm text-danger">{loadError}</p>
				{:else if !state.ready}
					<p class="absolute inset-0 flex items-center justify-center text-sm text-fg-muted">Opening image…</p>
				{/if}
			</div>

			{#if state.toolId === 'filters' && !phone}
				<div class="shrink-0 border-t border-line bg-surface-1">
					<FilterStrip {session} {state} />
				</div>
			{/if}

			<div class="md:hidden">
				{#if sheet}
					<div class="flex flex-col max-h-[42vh] border-t border-line-strong bg-surface-1 overflow-y-auto">
						<div class="sticky top-0 z-10 flex items-center justify-between px-3 py-1.5 border-b border-line bg-surface-1">
							<span class="text-sm font-semibold text-fg">{sheetTitle}</span>
							<ToolButton icon="M6 18L18 6M6 6l12 12" label="Close panel" compact onClick={closeSheet} />
						</div>
						{#if sheet === 'layers'}
							<LayersPanel {session} {state} onAddImage={() => openMenu('layer')} />
						{:else if sheet === 'canvas'}
							<CanvasPanel {session} {state} onOpenImage={() => openMenu('open')} />
						{:else}
							<ToolPanel {session} {state} {phone} onSaveFilter={() => (saveFilterOpen = true)} />
						{/if}
					</div>
				{:else if toolHasOptions}
					<OptionsPanel
						{tool}
						settings={state.settings}
						compact
						onSize={(value) => session.setSetting('size', value)}
						onOpacity={(value) => session.setSetting('opacity', value)}
						onColor={(value) => session.setColor(value)}
						onTolerance={(value) => session.setSetting('tolerance', value)}
					>
						<ToolButton icon={PAINT_ICONS.dots} label="More options" compact onClick={() => (sheet = 'tool')} />
					</OptionsPanel>
				{/if}
			</div>

			<div
				class="shrink-0 flex items-center gap-3 px-3 py-1 border-t border-line bg-surface-1 font-mono text-xs tabular-nums text-fg-subtle whitespace-nowrap overflow-hidden"
			>
				<span>{state.width} × {state.height}</span>
				{#if state.layers[state.activeIndex]}<span class="truncate">{state.layers[state.activeIndex].name}</span>{/if}
				{#if state.selectionSize}<span>sel {state.selectionSize.width} × {state.selectionSize.height}</span>{/if}
				{#if state.dirty}<span class="text-fg-muted">unsaved</span>{/if}
			</div>
		</div>

		{#if !panelHidden}
			<aside
				class="order-4 hidden md:block w-[17rem] shrink-0 border-l border-line bg-surface-1 overflow-y-auto"
				aria-label="Tool options, layers and canvas"
			>
				<ToolPanel {session} {state} onSaveFilter={() => (saveFilterOpen = true)} />
				<div class="border-t border-line">
					<LayersPanel {session} {state} onAddImage={() => openMenu('layer')} />
				</div>
				<div class="border-t border-line">
					<CanvasPanel {session} {state} onOpenImage={() => openMenu('open')} />
				</div>
			</aside>
		{/if}
	</div>

	<svelte:fragment slot="footer">
		<div class="relative flex flex-wrap items-center gap-2 w-full">
			{#if state.notice}
				<p class="min-w-0 flex-1 text-xs text-warning">{state.notice}</p>
			{:else}
				<p class="min-w-0 flex-1 text-xs text-fg-muted max-md:hidden">
					Saves as a new Library upload. The original is untouched.
				</p>
			{/if}

			<div class="ml-auto flex items-center gap-2 max-md:w-full max-md:[&>*]:flex-1">
				<Button variant="ghost" size="sm" onclick={requestClose} disabled={busy}>Cancel</Button>
				<Button variant="primary" size="sm" icon="check" onclick={openSave} disabled={busy || !state.ready}>
					Save as new
					<span class="ml-1 max-md:hidden"><Kbd keys="Ctrl+S" size="md" /></span>
				</Button>
			</div>

			{#if saveOpen}
				<SavePopover
					bind:this={popover}
					bind:stem
					meta="{state.width} × {state.height} · PNG · alpha kept"
					sourceName={lineageName}
					{decision}
					{busy}
					{failureMessage}
					onSave={save}
					onBack={() => (saveOpen = false)}
				/>
			{/if}

			{#if discardOpen}
				<div
					role="alertdialog"
					aria-label="Discard changes"
					class="absolute bottom-full right-3 mb-2 z-10 w-[21rem] max-w-[calc(100vw-1.5rem)] rounded-xl border border-line-strong bg-surface-1 shadow-overlay p-3.5 flex flex-col gap-3"
				>
					<p class="text-sm text-fg">Discard your changes? Nothing has been saved.</p>
					<div class="flex items-center justify-end gap-2">
						<Button variant="ghost" size="sm" onclick={() => (discardOpen = false)}>Keep editing</Button>
						<Button variant="danger" size="sm" onclick={onClose}>Discard</Button>
					</div>
				</div>
			{/if}
		</div>
	</svelte:fragment>
</EditorShell>

<SaveFilterDialog
	isOpen={saveFilterOpen}
	{session}
	{state}
	on:close={() => (saveFilterOpen = false)}
/>
