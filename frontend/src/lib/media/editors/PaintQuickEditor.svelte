<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import EditorShell from './EditorShell.svelte';
	import EditorSaveControls from './EditorSaveControls.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button, Input } from '$lib/components/ui';
	import { resumeKeyboard, suppressKeyboard } from '$lib/services/keyboard';
	import { isTypingInField } from '$lib/utils/editableTarget';
	import { primaryModifier } from '$lib/utils/platform';
	import CanvasPanel from '$lib/components/paint/CanvasPanel.svelte';
	import FilterStrip from '$lib/components/paint/FilterStrip.svelte';
	import OptionsPanel from '$lib/components/paint/OptionsPanel.svelte';
	import SaveFilterDialog from '$lib/components/paint/SaveFilterDialog.svelte';
	import { decideMaskFate } from '$lib/paint/maskPolicy';
	import { defaultExportStem, exportFileName } from '$lib/paint/exportName';
	import ImageSourceMenu from '$lib/components/paint/ImageSourceMenu.svelte';
	import LayersPanel from '$lib/components/paint/LayersPanel.svelte';
	import ToolButton from '$lib/components/paint/ToolButton.svelte';
	import ToolPanel from '$lib/components/paint/ToolPanel.svelte';
	import PaintStage from '$lib/components/tool-tabs/image/PaintStage.svelte';
	import type { DrawConfig } from '$lib/paint/drawConfig';
	import { PAINT_ICONS } from '$lib/paint/icons';
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

	type Sheet = 'tool' | 'layers' | 'canvas' | null;

	const session = new PaintSession();
	const modifier = primaryModifier();
	let stem = defaultExportStem(draw ? null : (source?.fileName ?? null));

	let state: SessionSnapshot = session.snapshot();
	let panelHidden = false;
	let fullScreen = false;
	let discardOpen = false;
	let saveFilterOpen = false;
	let phone = false;
	let phoneQuery: MediaQueryList | null = null;
	let menuMode: 'open' | 'layer' | null = null;
	let sheet: Sheet = null;
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
	$: toolHasOptions = (tool?.options?.length ?? 0) > 0;
	$: sheetTitle = sheet === 'layers' ? 'Layers' : sheet === 'canvas' ? 'Canvas' : (tool?.label ?? 'Options');
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
		sheet = null;
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


	const topButton =
		'inline-flex items-center justify-center gap-1.5 h-8 px-2 max-md:w-10 max-md:h-10 max-md:px-0 rounded border border-line-strong bg-surface-3 text-xs text-fg transition-colors hover:border-line-hover touch-manipulation';
</script>

<svelte:window on:keydown|capture={onKeydown} on:keyup|capture={onKeyup} />

{#snippet lead()}
	<div class="flex min-w-0 flex-1 basis-64 max-w-80 items-center gap-1.5">
		<label for="paint-save-name" class="shrink-0 text-sm text-fg-muted">File name</label>
		<Input id="paint-save-name" bind:value={stem} autocomplete="off" class="!min-h-8 !py-1 min-w-0 flex-1 text-sm" />
		<span class="shrink-0 font-mono text-sm text-fg-subtle">.png</span>
	</div>
{/snippet}

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
					kbd="{modifier}+Z"
					compact
					disabled={!state.canUndo}
					onClick={() => session.undo()}
				/>
				<ToolButton
					icon={PAINT_ICONS.redo}
					label={state.redoLabel ? `Redo ${state.redoLabel}` : 'Redo'}
					kbd="{modifier}+Y"
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

			<PaintStage {session} name={fileName} {prepare} onstate={onState} />

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
