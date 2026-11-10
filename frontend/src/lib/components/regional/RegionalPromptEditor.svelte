<script lang="ts">
	import IconButton from '$lib/components/ui/IconButton.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import type { CropRect } from '$lib/media/editors/cropGeometry';
	import {
		DRAW_THRESHOLD_PX,
		draftRect,
		drawRegion,
		formatCanvasSize,
		moveRegion,
		moveRegionTo,
		nudgeRegion,
		regionIndexLabel,
		removeRegionAt,
		replaceRegionAt,
		resizeRegion,
		selectionAfterMove,
		selectionAfterRemove,
		tagAbove,
		type CanvasSize,
		type Region,
		type RegionCorner
	} from '$lib/regional/regions';

	let {
		items,
		size,
		onChange
	}: {
		items: Region[];
		size: CanvasSize;
		onChange: (items: Region[]) => void;
	} = $props();

	const CORNERS: RegionCorner[] = ['nw', 'ne', 'sw', 'se'];
	const PLACEHOLDER = 'What this part of the image looks like';

	type Point = { x: number; y: number; px: number; py: number };
	type Drag =
		| { mode: 'move'; index: number; start: Point; origin: Region }
		| { mode: 'resize'; index: number; corner: RegionCorner; start: Point; origin: Region }
		| { mode: 'draw'; start: Point };

	let canvasEl = $state<HTMLDivElement | null>(null);
	let canvasHeight = $state(0);
	let selected = $state<number | null>(null);
	let live = $state<Region[] | null>(null);
	let draft = $state<CropRect | null>(null);
	let reorderFrom = $state<number | null>(null);
	let reorderOver = $state<number | null>(null);
	let armedIndex = $state<number | null>(null);
	let drag: Drag | null = null;
	const cardEls: Record<number, HTMLElement | undefined> = {};

	const shown = $derived(live ?? items);
	const aspect = $derived(size.width / size.height);
	const selectedIndex = $derived(selected !== null && selected < shown.length ? selected : null);

	function point(event: PointerEvent): Point {
		const rect = canvasEl!.getBoundingClientRect();
		const px = event.clientX - rect.left;
		const py = event.clientY - rect.top;
		return {
			x: Math.min(1, Math.max(0, px / rect.width)),
			y: Math.min(1, Math.max(0, py / rect.height)),
			px,
			py
		};
	}

	function reveal(index: number) {
		cardEls[index]?.scrollIntoView({ block: 'nearest' });
	}

	function onPointerDown(event: PointerEvent) {
		if (event.button !== 0 || !canvasEl) return;
		const target = event.target as HTMLElement;
		const box = target.closest<HTMLElement>('[data-region]');
		const start = point(event);
		if (box) {
			const index = Number(box.dataset.region);
			const origin = items[index];
			if (!origin) return;
			selected = index;
			reveal(index);
			const handle = target.closest<HTMLElement>('[data-corner]');
			drag = handle
				? { mode: 'resize', index, corner: handle.dataset.corner as RegionCorner, start, origin }
				: { mode: 'move', index, start, origin };
		} else {
			drag = { mode: 'draw', start };
		}
		canvasEl.setPointerCapture(event.pointerId);
		canvasEl.focus({ preventScroll: true });
		event.preventDefault();
	}

	function onPointerMove(event: PointerEvent) {
		if (!drag) return;
		const now = point(event);
		if (drag.mode === 'draw') {
			if (!draft && Math.hypot(now.px - drag.start.px, now.py - drag.start.py) < DRAW_THRESHOLD_PX) return;
			draft = draftRect(drag.start, now);
			return;
		}
		const dx = now.x - drag.start.x;
		const dy = now.y - drag.start.y;
		const next =
			drag.mode === 'move'
				? moveRegion(drag.origin, dx, dy)
				: resizeRegion(drag.origin, drag.corner, dx, dy);
		live = replaceRegionAt(items, drag.index, next);
	}

	function onPointerEnd(event: PointerEvent) {
		if (!drag) return;
		const done = drag;
		drag = null;
		if (canvasEl?.hasPointerCapture(event.pointerId)) canvasEl.releasePointerCapture(event.pointerId);
		if (done.mode === 'draw') {
			const created = draft ? drawRegion(done.start, point(event)) : null;
			draft = null;
			if (created) {
				onChange([...items, created]);
				selected = items.length;
				requestAnimationFrame(() => reveal(items.length - 1));
			} else if (!created && event.type === 'pointerup') {
				selected = null;
			}
			return;
		}
		const committed = live;
		live = null;
		if (committed && event.type === 'pointerup') onChange(committed);
	}

	function remove(index: number) {
		selected = selectionAfterRemove(items.length, index, selected);
		onChange(removeRegionAt(items, index));
	}

	function isTyping(target: EventTarget | null): boolean {
		const el = target as HTMLElement | null;
		return !!el && (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT' || el.tagName === 'SELECT' || el.isContentEditable);
	}

	function onKeyDown(event: KeyboardEvent) {
		if (isTyping(event.target)) return;
		if (event.key === 'Escape') {
			if (selectedIndex !== null) {
				selected = null;
				event.stopPropagation();
			}
			return;
		}
		if (selectedIndex === null) return;
		if (event.key === 'Delete' || event.key === 'Backspace') {
			event.preventDefault();
			remove(selectedIndex);
			return;
		}
		const next = nudgeRegion(items[selectedIndex], event.key, event.shiftKey);
		if (!next) return;
		event.preventDefault();
		onChange(replaceRegionAt(items, selectedIndex, next));
	}

	function setCaption(index: number, caption: string) {
		onChange(replaceRegionAt(items, index, { ...items[index], caption }));
	}

	function selectCard(index: number, event: MouseEvent) {
		selected = index;
		const target = event.target as HTMLElement;
		if (!target.closest('textarea, button')) canvasEl?.focus({ preventScroll: true });
	}

	function onDragStart(event: DragEvent, index: number) {
		if (armedIndex !== index) {
			event.preventDefault();
			return;
		}
		reorderFrom = index;
		event.dataTransfer?.setData('text/plain', String(index));
		if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
	}

	function onDragOver(event: DragEvent, index: number) {
		if (reorderFrom === null) return;
		event.preventDefault();
		reorderOver = index;
	}

	function onDrop(event: DragEvent, index: number) {
		if (reorderFrom === null) return;
		event.preventDefault();
		const from = reorderFrom;
		selected = selectionAfterMove(selected, from, index);
		onChange(moveRegionTo(items, from, index));
		endReorder();
	}

	function endReorder() {
		reorderFrom = null;
		reorderOver = null;
		armedIndex = null;
	}

	function autosize(node: HTMLTextAreaElement, _value: string) {
		const fit = () => {
			node.style.height = 'auto';
			node.style.height = `${Math.max(36, node.scrollHeight + 2)}px`;
		};
		fit();
		return { update: fit };
	}

	function listen<K extends keyof HTMLElementEventMap>(
		node: HTMLElement,
		[type, handler]: [K, (event: HTMLElementEventMap[K]) => void]
	) {
		node.addEventListener(type, handler as EventListener);
		return { destroy: () => node.removeEventListener(type, handler as EventListener) };
	}

	function percent(value: number): string {
		return `${value * 100}%`;
	}
</script>

<svelte:window onpointerup={() => (armedIndex = null)} />

<div class="regional-host" data-testid="regional-editor">
	<div class="regional" use:listen={['keydown', onKeyDown]} style:--ar={aspect}>
		<div class="rg-cv-col">
			<div
				bind:this={canvasEl}
				bind:clientHeight={canvasHeight}
				class="rg-cv"
				role="application"
				tabindex="0"
				aria-label="Regions canvas, {formatCanvasSize(size)}. Drag to place a region. Arrow keys move the selected region, Delete removes it, Escape deselects."
				data-testid="regional-canvas"
				onpointerdown={onPointerDown}
				onpointermove={onPointerMove}
				onpointerup={onPointerEnd}
				onpointercancel={onPointerEnd}
			>
				{#each shown as region, index (index)}
					<div
						class="rg-box"
						class:sel={index === selectedIndex}
						class:above={tagAbove(region, canvasHeight)}
						data-region={index}
						style:left={percent(region.x)}
						style:top={percent(region.y)}
						style:width={percent(region.w)}
						style:height={percent(region.h)}
					>
						<span class="rg-tag">{index + 1}</span>
						{#each CORNERS as corner (corner)}
							<i class="rg-handle {corner}" data-corner={corner}></i>
						{/each}
					</div>
				{/each}
				{#if draft}
					<div
						class="rg-box draft"
						style:left={percent(draft.x)}
						style:top={percent(draft.y)}
						style:width={percent(draft.width)}
						style:height={percent(draft.height)}
					></div>
				{/if}
				{#if shown.length === 0 && !draft}
					<div class="rg-empty">
						<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="1" /></svg>
						<span>Drag to place a region</span>
					</div>
				{/if}
				<span class="rg-size" data-testid="regional-size">{formatCanvasSize(size)}</span>
			</div>
		</div>

		<div class="rg-list" data-testid="regional-list">
			{#if shown.length === 0}
				<p class="rg-note">The prompt above describes the whole image. Regions add what goes where.</p>
			{:else}
				{#each shown as region, index (index)}
					<div
						bind:this={cardEls[index]}
						class="rg-card"
						role="group"
						aria-label="Region {index + 1}"
						class:sel={index === selectedIndex}
						class:over={reorderOver === index && reorderFrom !== index}
						class:dragging={reorderFrom === index}
						data-card={index}
						draggable={armedIndex === index}
						use:listen={['click', (event) => selectCard(index, event)]}
						onfocusin={() => (selected = index)}
						ondragstart={(event) => onDragStart(event, index)}
						ondragover={(event) => onDragOver(event, index)}
						ondrop={(event) => onDrop(event, index)}
						ondragend={endReorder}
					>
						<div class="rg-head">
							<span
								class="rg-grip"
								aria-hidden="true"
								onpointerdown={() => (armedIndex = index)}
							>
								<Icon name="composer-grip" className="rg-icon" strokeWidth={1.7} />
							</span>
							<span class="rg-idx">{regionIndexLabel(index)}</span>
							<span class="rg-spacer"></span>
							<IconButton
								icon="composer-trash"
								size="xs"
								label="Remove region"
								class="hover:!text-danger"
								aria-label="Remove region {index + 1}"
								onclick={() => remove(index)}
							/>
						</div>
						<div class="rg-body">
							<textarea
								class="rg-field"
								rows="2"
								placeholder={PLACEHOLDER}
								aria-label="Region {index + 1} description"
								value={region.caption}
								use:autosize={region.caption}
								oninput={(event) => setCaption(index, event.currentTarget.value)}
							></textarea>
						</div>
					</div>
				{/each}
			{/if}
		</div>
	</div>
</div>

<style>
	.regional-host {
		container-type: inline-size;
		min-width: 0;
	}

	.regional {
		display: flex;
		flex-direction: column;
		gap: 12px;
		--maxh: 300px;
	}

	.rg-cv-col {
		display: flex;
		justify-content: center;
	}

	.rg-cv {
		position: relative;
		width: min(100%, calc(var(--maxh) * var(--ar)));
		aspect-ratio: var(--ar);
		border: 1px solid rgb(var(--line-strong));
		border-radius: 6px;
		background-color: rgb(var(--canvas));
		background-image: radial-gradient(rgb(var(--line-strong)) 1px, transparent 1px);
		background-size: 14px 14px;
		background-position: 6px 6px;
		cursor: crosshair;
		touch-action: none;
		user-select: none;
		-webkit-user-select: none;
		outline: none;
	}

	.rg-cv:focus-visible {
		outline: 2px solid rgb(var(--signal));
		outline-offset: 2px;
	}

	.rg-size {
		position: absolute;
		right: 6px;
		bottom: 6px;
		z-index: 5;
		padding: 0 6px;
		border-radius: 4px;
		background: rgb(var(--surface-2));
		color: rgb(var(--fg-muted));
		font: 400 12px/18px 'IBM Plex Mono', monospace;
		font-variant-numeric: tabular-nums;
		pointer-events: none;
		box-shadow: inset 0 0 0 1px rgb(var(--line));
	}

	.rg-empty {
		position: absolute;
		inset: 0;
		display: grid;
		place-content: center;
		justify-items: center;
		gap: 8px;
		color: rgb(var(--fg-subtle));
		font-size: 13px;
		pointer-events: none;
	}

	.rg-empty svg {
		width: 22px;
		height: 22px;
		fill: none;
		stroke: currentColor;
		stroke-width: 1.5;
		stroke-dasharray: 3 3;
	}

	.rg-box {
		position: absolute;
		border: 2px solid rgb(var(--line-hover));
		border-radius: 2px;
		background: rgb(var(--fg) / 0.05);
		cursor: move;
	}

	.rg-box:hover {
		border-color: rgb(var(--fg-subtle));
	}

	.rg-box.sel {
		z-index: 3;
		border-color: rgb(var(--signal));
		background: rgb(var(--signal) / 0.16);
	}

	.rg-box.draft {
		z-index: 4;
		border-style: dashed;
		border-color: rgb(var(--signal));
		background: rgb(var(--signal) / 0.1);
		pointer-events: none;
	}

	.rg-tag {
		position: absolute;
		left: 3px;
		top: 3px;
		max-width: calc(100% - 6px);
		display: inline-flex;
		align-items: center;
		height: 20px;
		padding: 0 6px 0 5px;
		overflow: hidden;
		border-radius: 3px;
		background: rgb(var(--surface-3));
		color: rgb(var(--fg-muted));
		font: 600 12px/16px 'IBM Plex Mono', monospace;
		font-variant-numeric: tabular-nums;
		box-shadow: inset 0 0 0 1px rgb(var(--line-strong));
		pointer-events: none;
	}

	.rg-box.sel .rg-tag {
		background: rgb(var(--signal) / 0.2);
		color: rgb(var(--signal));
		box-shadow: inset 0 0 0 1px rgb(var(--signal) / 0.5);
	}

	.rg-box.above .rg-tag {
		top: -24px;
		left: -2px;
	}

	.rg-handle {
		display: none;
		position: absolute;
		width: 10px;
		height: 10px;
		border: 2px solid rgb(var(--signal));
		border-radius: 2px;
		background: rgb(var(--surface-1));
	}

	.rg-handle::after {
		content: '';
		position: absolute;
		inset: -9px;
	}

	.rg-box.sel .rg-handle {
		display: block;
	}

	.rg-handle.nw {
		left: -6px;
		top: -6px;
		cursor: nwse-resize;
	}

	.rg-handle.ne {
		right: -6px;
		top: -6px;
		cursor: nesw-resize;
	}

	.rg-handle.sw {
		left: -6px;
		bottom: -6px;
		cursor: nesw-resize;
	}

	.rg-handle.se {
		right: -6px;
		bottom: -6px;
		cursor: nwse-resize;
	}

	.rg-list {
		display: flex;
		flex-direction: column;
		gap: 8px;
		min-width: 0;
	}

	.rg-note {
		margin: 0;
		padding: 12px;
		border: 1px dashed rgb(var(--line-strong));
		border-radius: 6px;
		color: rgb(var(--fg-muted));
		font-size: 13px;
		line-height: 20px;
	}

	.rg-card {
		position: relative;
		border: 1px solid rgb(var(--line));
		border-radius: 6px;
		background: rgb(var(--surface-1));
		transition: border-color 0.15s;
	}

	.rg-card:hover {
		border-color: rgb(var(--line-strong));
	}

	.rg-card.sel,
	.rg-card.sel:hover,
	.rg-card.sel:focus-within {
		border-color: rgb(var(--signal));
		box-shadow: 0 0 0 1px rgb(var(--signal) / 0.35);
	}

	.rg-card.over {
		border-color: rgb(var(--signal));
		border-style: dashed;
	}

	.rg-card.dragging {
		opacity: 0.5;
	}

	.rg-head {
		display: flex;
		align-items: center;
		gap: 1px;
		padding: 6px 6px 0 7px;
	}

	.rg-grip {
		width: 22px;
		height: 22px;
		display: grid;
		place-items: center;
		border-radius: 4px;
		color: rgb(var(--fg-subtle));
		cursor: grab;
	}

	.rg-grip:hover {
		color: rgb(var(--fg));
		background: rgb(var(--surface-2));
	}

	.rg-grip :global(.rg-icon) {
		width: 13px;
		height: 13px;
	}

	.rg-idx {
		margin: 0 3px 0 1px;
		font: 400 12px/16px 'IBM Plex Mono', monospace;
		font-variant-numeric: tabular-nums;
		color: rgb(var(--fg-subtle));
	}

	.rg-card.sel .rg-idx {
		color: rgb(var(--signal));
	}

	.rg-spacer {
		flex: 1 1 0;
	}

	.rg-body {
		padding: 4px 10px 10px;
	}

	.rg-field {
		display: block;
		width: 100%;
		min-height: 36px;
		padding: 7px 10px;
		resize: none;
		overflow: hidden;
		border: 1px solid rgb(var(--field-border));
		border-radius: 4px;
		background: rgb(var(--field-bg));
		color: rgb(var(--fg));
		font-size: 13px;
		line-height: 20px;
	}

	.rg-field::placeholder {
		color: rgb(var(--fg-subtle));
	}

	.rg-field:focus {
		outline: none;
		border-color: rgb(var(--signal));
	}

	@container (min-width: 620px) {
		.regional {
			flex-direction: row;
			align-items: flex-start;
			gap: 16px;
			--maxh: 360px;
		}

		.rg-cv-col {
			flex: 0 0 auto;
			width: min(52%, calc(var(--maxh) * var(--ar)));
			position: sticky;
			top: 0;
		}

		.rg-cv {
			width: 100%;
		}

		.rg-list {
			flex: 1;
		}
	}
</style>
