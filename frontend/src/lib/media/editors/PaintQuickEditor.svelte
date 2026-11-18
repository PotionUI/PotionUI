<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import EditorShell from './EditorShell.svelte';
	import EditorSaveControls from './EditorSaveControls.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, IconButton, Input } from '$lib/components/ui';
	import ToolRail from '$lib/components/tool-tabs/frame/ToolRail.svelte';
	import PaintOptions from '$lib/components/tool-tabs/image/PaintOptions.svelte';
	import PaintMenuBar from '$lib/components/paint/PaintMenuBar.svelte';
	import PaintPanel from '$lib/components/paint/PaintPanel.svelte';
	import PaintToolPanel from '$lib/components/paint/PaintToolPanel.svelte';
	import CanvasSizePanel from '$lib/components/paint/CanvasSizePanel.svelte';
	import { paintIconName } from '$lib/paint/entries';
	import { modalRailGroups } from '$lib/paint/railGroups';
	import { paintToolForKey } from '$lib/paint/toolKeys';
	import type { LayerAction } from '$lib/paint/layerActions';
	import { resumeKeyboard, suppressKeyboard } from '$lib/services/keyboard';
	import { isTypingInField } from '$lib/utils/editableTarget';
	import FilterStrip from '$lib/components/paint/FilterStrip.svelte';
	import SaveFilterDialog from '$lib/components/paint/SaveFilterDialog.svelte';
	import { decideMaskFate } from '$lib/paint/maskPolicy';
	import { defaultExportStem, exportFileName } from '$lib/paint/exportName';
	import ImageSourceMenu from '$lib/components/paint/ImageSourceMenu.svelte';
	import PaintStage from '$lib/components/tool-tabs/image/PaintStage.svelte';
	import type { DrawConfig } from '$lib/paint/drawConfig';
	import { PaintSession, type SessionSnapshot } from '$lib/paint/session';
	import type { PickedImage } from '$lib/paint/types';
	import type { EditorCommitFn, MediaEditorSource } from './types';

	export let source: MediaEditorSource | null = null;
	export let draw: DrawConfig | null = null;
	export let hasMask: boolean = false;
	export let busy: boolean = false;
	export let failureMessage: string | null = null;
	export let onClose: () => void;
	export let commit: EditorCommitFn;

	type LayerPanel = 'adjust' | 'filters' | 'canvas' | null;

	const session = new PaintSession();
	let stem = defaultExportStem(draw ? null : (source?.fileName ?? null));

	let state: SessionSnapshot = session.snapshot();
	let panelHidden = false;
	let fullScreen = false;
	let discardOpen = false;
	let saveFilterOpen = false;
	let phone = false;
	let phoneQuery: MediaQueryList | null = null;
	let menuMode: 'open' | 'layer' | null = null;
	let layerPanel: LayerPanel = null;
	let returnTool = 'brush';
	let themeObserver: MutationObserver | null = null;

	function syncPhone() {
		phone = phoneQuery?.matches ?? false;
	}

	function onState(_session: PaintSession, next: SessionSnapshot) {
		state = next;
	}

	async function prepare(target: PaintSession) {
		if (draw) {
			target.openBlank({
				width: draw.width,
				height: draw.height,
				background: draw.background,
				pen: draw.pen
			});
			if (draw.scribble) target.setSetting('size', 6);
		} else if (source) {
			await target.openSource(source.url, source.fileName);
		}
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
	$: zoomLabel = `${Math.round(state.zoom * 100)}%`;
	$: title = draw ? 'Draw' : 'Edit image';
	$: fileName = state.sourceName ?? source?.fileName ?? 'Untitled drawing';
	$: rail = modalRailGroups(state.tools);
	$: railActive = layerPanel === 'adjust' || layerPanel === 'filters' ? returnTool : state.toolId;
	$: returnLabel = state.tools.find((candidate) => candidate.id === returnTool)?.label ?? 'Back';
	$: panelTitle = layerPanel === 'adjust' ? 'Adjust colours' : layerPanel === 'filters' ? 'Apply a filter' : layerPanel === 'canvas' ? 'Canvas size' : (tool?.label ?? 'Options');
	$: panelIcon = layerPanel ? 'adjustments-horizontal' : paintIconName(state.toolId);
	$: layerActions = [
		{ id: 'adjust', label: 'Adjust colours…', icon: 'adjustments-horizontal', run: () => openLayerPanel('adjust') },
		{ id: 'filter', label: 'Apply a filter…', icon: 'sparkles', run: () => openLayerPanel('filters') }
	] satisfies LayerAction[];
	$: note = decision.notice ?? state.notice;
	$: blocked = state.ready ? null : 'Opening image…';

	onMount(() => {
		suppressKeyboard();
		if (typeof matchMedia === 'function') {
			phoneQuery = matchMedia('(max-width: 767px)');
			syncPhone();
			phoneQuery.addEventListener?.('change', syncPhone);
		}
		themeObserver = new MutationObserver(() => session.refreshTheme());
		themeObserver.observe(document.documentElement, {
			attributes: true,
			attributeFilter: ['data-theme']
		});
	});

	onDestroy(() => {
		resumeKeyboard();
		phoneQuery?.removeEventListener?.('change', syncPhone);
		themeObserver?.disconnect();
	});

	function openLayerPanel(next: Exclude<LayerPanel, null>) {
		if (next !== 'canvas') {
			if (layerPanel === null) returnTool = state.toolId === 'adjust' || state.toolId === 'filters' ? returnTool : state.toolId;
			session.setTool(next);
		}
		layerPanel = next;
	}

	function backFromPanel() {
		const was = layerPanel;
		layerPanel = null;
		if (was === 'adjust' || was === 'filters') session.setTool(returnTool);
	}

	function selectTool(id: string) {
		if (id === 'adjust' || id === 'filters') {
			openLayerPanel(id);
			return;
		}
		layerPanel = null;
		session.setTool(id);
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
		if (discardOpen) {
			discardOpen = false;
			return;
		}
		if (session.cancelSelectionStroke()) return;
		if (layerPanel) {
			backFromPanel();
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
		discardOpen = false;
		menuMode = menuMode === mode ? null : mode;
	}

	function picked(image: PickedImage) {
		if (menuMode === 'open') session.openImage(image.canvas, image.name);
		else session.addImage(image.canvas, image.name);
		menuMode = null;
	}

	function onKeydown(event: KeyboardEvent) {
		if (isTypingInField(event)) return;
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
				void save();
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
			} else if (state.floatingId !== null) {
				event.preventDefault();
				session.placeFloating();
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
			const match = paintToolForKey(event, state.tools);
			if (match) selectTool(match);
		}
	}

	function onKeyup(event: KeyboardEvent) {
		if (event.key === ' ') session.setSpaceHeld(false);
		if (event.key === '\\') session.setFilterCompare(false);
	}


	function zoomActual() {
		session.zoomBy(1 / session.zoom);
	}
</script>

<svelte:window on:keydown|capture={onKeydown} on:keyup|capture={onKeyup} />

{#snippet lead()}
	<div class="flex min-w-0 flex-1 basis-64 max-w-80 items-center gap-1.5">
		<label for="paint-save-name" class="shrink-0 text-sm text-fg-muted">File name</label>
		<Input id="paint-save-name" bind:value={stem} autocomplete="off" class="!min-h-8 !py-1 min-w-0 flex-1 text-sm" />
		<span class="shrink-0 font-mono text-sm text-fg-subtle">.png</span>
	</div>
{/snippet}

{#snippet panelBody()}
	{#if layerPanel}
		<div class="flex flex-col gap-3" data-paint-layer-panel={layerPanel}>
			<div class="flex items-center gap-2">
				<button type="button" class="inline-flex h-8 items-center gap-1.5 rounded px-1.5 text-base text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline focus-visible:outline-1 focus-visible:outline-signal" aria-label="Back to {returnLabel}" data-paint-back on:click={backFromPanel}>
					<Icon name="arrow-left" className="h-4 w-4" />
					{returnLabel}
				</button>
				<span class="text-base font-semibold text-fg">{panelTitle}</span>
			</div>
			{#if layerPanel === 'canvas'}
				<CanvasSizePanel {session} {state} />
			{:else}
				<PaintToolPanel {session} {state} {phone} onSaveFilter={() => (saveFilterOpen = true)} />
			{/if}
		</div>
	{:else if phone}
		<PaintOptions {session} {state} phone {layerActions} onSaveFilter={() => (saveFilterOpen = true)} />
	{:else}
		<PaintPanel {session} {state} {layerActions} onSaveFilter={() => (saveFilterOpen = true)} />
	{/if}
{/snippet}

<EditorShell
	{title}
	{fileName}
	icon="brush"
	widthClass={sizeClass}
	{busy}
	onClose={requestClose}
>
	<div class="flex flex-col md:flex-row h-full min-h-0 bg-canvas" data-paint-modal>
		<div class="order-3 md:order-1 flex flex-none min-h-0">
			<ToolRail groups={rail} active={railActive} variant={phone ? 'strip' : 'icons'} groupedStrip label="Paint tools" onselect={selectTool} />
		</div>

		{#if !panelHidden && !phone}
			<section class="order-2 hidden md:flex w-[17rem] flex-none flex-col border-r border-line bg-surface-1" aria-label="Tool options, layers and canvas" data-paint-panel-column>
				<div class="flex h-12 flex-none items-center gap-2 border-b border-line px-4">
					<Icon name={panelIcon} className="h-3.5 w-3.5 flex-none text-fg-muted" />
					<h2 class="min-w-0 flex-1 truncate text-base font-semibold text-fg">{panelTitle}</h2>
				</div>
				<div class="min-h-0 flex-1 overflow-y-auto p-4" data-paint-panel-body>
					{@render panelBody()}
				</div>
			</section>
		{/if}

		<div class="order-1 md:order-3 flex flex-col flex-1 min-w-0 min-h-0">
			<div class="relative shrink-0 flex items-center gap-1 px-2 py-1 border-b border-line bg-surface-1">
				<PaintMenuBar
					{session}
					{state}
					{rail}
					activeTool={railActive}
					{panelHidden}
					{fullScreen}
					onOpenImage={() => openMenu('open')}
					onAddImage={() => openMenu('layer')}
					onCanvasSize={() => openLayerPanel('canvas')}
					onSelectTool={selectTool}
					onTogglePanel={() => (panelHidden = !panelHidden)}
					onToggleFull={() => (fullScreen = !fullScreen)}
				/>

				{#if menuMode}
					<ImageSourceMenu
						mode={menuMode}
						dirty={state.dirty}
						onPick={picked}
						onClose={() => (menuMode = null)}
					/>
				{/if}
			</div>

			<div class="relative flex min-h-0 flex-1 flex-col">
				<PaintStage {session} name={fileName} {prepare} onstate={onState} />
				<div class="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center" data-paint-zoombar-host>
					<div class="pointer-events-auto flex items-center gap-0.5 rounded-lg border border-line-strong bg-surface-2 p-0.5 shadow-floating" role="group" aria-label="Zoom">
						<IconButton icon="minus" label="Zoom out" kbd="-" size="sm" onclick={() => session.zoomBy(1 / 1.25)} />
						<span class="w-12 text-center font-mono text-sm tabular-nums text-fg" data-zoom-readout>{zoomLabel}</span>
						<IconButton icon="plus" label="Zoom in" kbd="+" size="sm" onclick={() => session.zoomBy(1.25)} />
						<span class="mx-0.5 h-5 w-px bg-line" aria-hidden="true"></span>
						<button type="button" class="h-8 rounded px-2.5 font-mono text-sm text-fg-muted hover:bg-surface-3/50 hover:text-fg focus-visible:outline focus-visible:outline-1 focus-visible:outline-signal" aria-label="Fit to view" on:click={() => session.fit()}>Fit</button>
						<button type="button" class="h-8 rounded px-2.5 font-mono text-sm text-fg-muted hover:bg-surface-3/50 hover:text-fg focus-visible:outline focus-visible:outline-1 focus-visible:outline-signal" aria-label="Actual size" on:click={zoomActual}>1:1</button>
					</div>
				</div>
			</div>

			{#if state.toolId === 'filters' && !phone}
				<div class="shrink-0 border-t border-line bg-surface-1">
					<FilterStrip {session} {state} />
				</div>
			{/if}

			{#if phone && !panelHidden}
				<div class="max-h-[42vh] flex-none overflow-y-auto border-t border-line-strong bg-surface-1 p-3" data-paint-phone-dock>
					{@render panelBody()}
				</div>
			{/if}

			<div
				class="shrink-0 flex items-center gap-3 px-3 py-1 border-t border-line bg-surface-1 font-mono text-sm tabular-nums text-fg-subtle whitespace-nowrap overflow-hidden"
				data-paint-status
			>
				<span>{state.width} × {state.height}</span>
				{#if state.layers[state.activeIndex]}<span class="truncate">{state.layers[state.activeIndex].name}</span>{/if}
				{#if state.selectionSize}<span>sel {state.selectionSize.width} × {state.selectionSize.height}</span>{/if}
				{#if state.dirty}<span class="text-fg-muted">unsaved</span>{/if}
				<span class="flex-1"></span>
				<span class="max-md:hidden">Space drag to pan</span>
			</div>
		</div>
	</div>

	<svelte:fragment slot="footer">
		<div class="relative w-full">
			<EditorSaveControls
				applyLabel="Save"
				applyIcon
				allowReplace={false}
				{busy}
				blockedReason={blocked}
				{failureMessage}
				{note}
				{lead}
				onCancel={requestClose}
				onSave={() => void save()}
			/>

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
