<script lang="ts">
	import { chipIndicatorColor } from '$lib/utils/chipIndicatorColor';
	import {
		sortByStart,
		buildTicks,
		mintId,
		attachDrag,
		totalWidth as computeTotalWidth,
		dragSegment,
		trimSegmentLeft,
		trimSegmentRight,
		neighborBounds,
		findSegmentGap,
		stepZoom,
		isZoomGestureEvent,
		vizSlot,
		DEFAULT_ZOOM
	} from '../video-director/timelineCore';

	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;

	$: label = config.title || name || '';
	$: description = config.description || '';

	// ─── Types ─────────────────────────────────────────────────────────────────
	interface Segment {
		id: string;
		start: number;
		end: number;
		text: string;
	}

	interface TimelineValue {
		duration: number;
		fps: number;
		segments: Segment[];
	}

	// Segment colour: cycles through the colorblind-safe --viz-1..--viz-8 series
	// (chipIndicatorColor's slot idea) — solid for the border, 15% for the fill.
	function colorForIndex(i: number) {
		return { border: chipIndicatorColor(i), bg: `rgb(var(--viz-${vizSlot(i)}) / 0.15)` };
	}

	// ─── Defensive value initialisation ────────────────────────────────────────
	function parseValue(raw: any): TimelineValue {
		if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
			return {
				duration: typeof raw.duration === 'number' ? raw.duration : (config.duration ?? 5),
				fps: typeof raw.fps === 'number' ? raw.fps : (config.fps ?? 25),
				segments: Array.isArray(raw.segments) ? raw.segments : [],
			};
		}
		return {
			duration: config.duration ?? 5,
			fps: config.fps ?? 25,
			segments: [],
		};
	}

	let tv: TimelineValue = parseValue(value);

	// Keep tv in sync when the external value changes (e.g. preset reload)
	$: {
		const next = parseValue(value);
		// Only re-sync if the parsed result is structurally different from what we
		// already have — this avoids clobbering local edits on every re-render.
		if (
			next.duration !== tv.duration ||
			next.fps !== tv.fps ||
			JSON.stringify(next.segments) !== JSON.stringify(tv.segments)
		) {
			tv = next;
		}
	}

	// ─── UI state ───────────────────────────────────────────────────────────────
	let zoom: number = config.zoom ?? DEFAULT_ZOOM; // pixels per second
	let selectedId: string | null = null;

	$: selectedSegment = tv.segments.find((s) => s.id === selectedId) ?? null;

	// ─── Ruler ticks ────────────────────────────────────────────────────────────
	$: ticks = buildTicks(tv.duration, zoom);

	$: totalWidth = computeTotalWidth(tv.duration, zoom);

	// ─── Emit helper ─────────────────────────────────────────────────────────────
	function emit(updated: TimelineValue) {
		tv = updated;
		if (name) {
			onChange(name, updated);
		}
	}

	function addSegment() {
		const gap = findSegmentGap(sortByStart(tv.segments), tv.duration);
		if (!gap) return; // no room at all

		const newSeg: Segment = { id: mintId('seg', tv.segments), ...gap, text: '' };

		const newSegments = sortByStart([...tv.segments, newSeg]);
		selectedId = newSeg.id;
		emit({ ...tv, segments: newSegments });
	}

	function deleteSelected() {
		if (!selectedId) return;
		const newSegments = tv.segments.filter((s) => s.id !== selectedId);
		selectedId = null;
		emit({ ...tv, segments: newSegments });
	}

	function updateText(id: string, text: string) {
		const newSegments = tv.segments.map((s) => (s.id === id ? { ...s, text } : s));
		emit({ ...tv, segments: newSegments });
	}

	// ─── Drag to move ────────────────────────────────────────────────────────────
	function handleSegmentMouseDown(e: MouseEvent, segId: string) {
		if (e.button !== 0) return;
		e.stopPropagation();
		selectedId = segId;

		const seg = tv.segments.find((s) => s.id === segId);
		if (!seg) return;

		const startX = e.clientX;
		const origStart = seg.start;
		const origEnd = seg.end;

		function onMove(ev: MouseEvent) {
			const { start, end } = dragSegment(origStart, origEnd, ev.clientX - startX, zoom, tv.duration);
			const newSegments = tv.segments.map((s) => (s.id === segId ? { ...s, start, end } : s));
			emit({ ...tv, segments: newSegments });
		}

		attachDrag(onMove);
	}

	// ─── Left trim handle ────────────────────────────────────────────────────────
	function handleLeftTrimMouseDown(e: MouseEvent, segId: string) {
		e.stopPropagation();
		selectedId = segId;

		const seg = tv.segments.find((s) => s.id === segId);
		if (!seg) return;

		const startX = e.clientX;
		const origStart = seg.start;
		const origEnd = seg.end;

		const sorted = sortByStart(tv.segments);
		const { leftBound } = neighborBounds(sorted, segId, tv.duration);

		function onMove(ev: MouseEvent) {
			const start = trimSegmentLeft(origStart, origEnd, ev.clientX - startX, zoom, leftBound);
			const newSegments = tv.segments.map((s) => (s.id === segId ? { ...s, start } : s));
			emit({ ...tv, segments: newSegments });
		}

		attachDrag(onMove);
	}

	// ─── Right trim handle ───────────────────────────────────────────────────────
	function handleRightTrimMouseDown(e: MouseEvent, segId: string) {
		e.stopPropagation();
		selectedId = segId;

		const seg = tv.segments.find((s) => s.id === segId);
		if (!seg) return;

		const startX = e.clientX;
		const origStart = seg.start;
		const origEnd = seg.end;

		const sorted = sortByStart(tv.segments);
		const { rightBound } = neighborBounds(sorted, segId, tv.duration);

		function onMove(ev: MouseEvent) {
			const end = trimSegmentRight(origStart, origEnd, ev.clientX - startX, zoom, rightBound);
			const newSegments = tv.segments.map((s) => (s.id === segId ? { ...s, end } : s));
			emit({ ...tv, segments: newSegments });
		}

		attachDrag(onMove);
	}

	// ─── Zoom controls ───────────────────────────────────────────────────────────
	function zoomIn() {
		zoom = stepZoom(zoom, 1);
	}

	function zoomOut() {
		zoom = stepZoom(zoom, -1);
	}

	function handleWheel(e: WheelEvent) {
		if (isZoomGestureEvent(e)) {
			e.preventDefault();
			if (e.deltaY < 0) zoomIn();
			else zoomOut();
		}
	}

	// ─── Track click to deselect ─────────────────────────────────────────────────
	// Only deselect when the empty track background itself is clicked — a click on a
	// segment bubbles up here, and deselecting then would instantly close the editor.
	function handleTrackClick(e: MouseEvent) {
		if (e.target === e.currentTarget) {
			selectedId = null;
		}
	}

	// ─── Segment index lookup (for colour) ───────────────────────────────────────
	// Chronological index per segment, keyed by segment id (each-blocks below are
	// keyed by `seg.id`). `tv` gets reassigned on every reorder/edit; this is a `$:`
	// statement (not a plain function called from `{@const}`) so Svelte's dependency
	// scan sees `tv` directly and recomputes when segments move — a function call
	// hides that read and the color would freeze at its pre-reorder value.
	$: segmentColorIndexById = new Map(sortByStart(tv.segments).map((s, idx) => [s.id, idx]));
</script>

<div class="field-card">
	<!-- Header row -->
	<div class="pt-header">
		<div class="flex items-center gap-2">
			<span class="label !mb-0">{label}</span>
			<span class="pt-meta">{tv.duration}s · {tv.fps} fps · {tv.segments.length} segment{tv.segments.length !== 1 ? 's' : ''}</span>
		</div>
		<div class="pt-toolbar-right">
			<!-- Zoom controls -->
			<div class="pt-zoom">
				<button type="button" class="pt-zoom-btn" on:click={zoomOut} title="Zoom out" aria-label="Zoom out">−</button>
				<span class="pt-zoom-label">{zoom}px/s</span>
				<button type="button" class="pt-zoom-btn" on:click={zoomIn} title="Zoom in" aria-label="Zoom in">+</button>
			</div>
			<!-- Add segment -->
			<button type="button" class="pt-add-btn" on:click={addSegment} aria-label="Add segment">
				<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
					<path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4" />
				</svg>
				Add Segment
			</button>
		</div>
	</div>

	{#if description}
		<p class="text-xs text-fg-muted mt-1 mb-2">{description}</p>
	{/if}

	<!-- Timeline viewport -->
	<!-- svelte-ignore a11y-no-static-element-interactions -->
	<div class="pt-viewport" on:wheel={handleWheel}>
		<!-- Time ruler -->
		<div class="pt-ruler" style="width: {totalWidth}px;">
			{#each ticks as tick}
				<div class="pt-tick" style="left: {tick.time * zoom}px;">
					<span class="pt-tick-label">{tick.label}</span>
					<div class="pt-tick-line"></div>
				</div>
			{/each}
			<!-- Duration end marker -->
			<div class="pt-end-marker" style="left: {tv.duration * zoom}px;" title="End: {tv.duration}s"></div>
		</div>

		<!-- Track lane -->
		<!-- svelte-ignore a11y-click-events-have-key-events -->
		<!-- svelte-ignore a11y-no-static-element-interactions -->
		<div
			class="pt-track"
			style="width: {totalWidth}px;"
			on:click={handleTrackClick}
		>
			{#each tv.segments as seg, i (seg.id)}
				{@const colorIdx = segmentColorIndexById.get(seg.id) ?? -1}
				{@const color = colorForIndex(colorIdx)}
				{@const isSelected = selectedId === seg.id}
				{@const left = seg.start * zoom}
				{@const width = Math.max((seg.end - seg.start) * zoom, 4)}
				{@const labelText = seg.text.length > 40 ? seg.text.slice(0, 40) + '…' : seg.text || `Segment ${colorIdx + 1}`}

				<!-- svelte-ignore a11y-no-static-element-interactions -->
				<div
					class="pt-clip"
					class:pt-clip--selected={isSelected}
					style="left: {left}px; width: {width}px; background: {color.bg}; border-color: {isSelected ? color.border : 'rgb(var(--line-strong))'};"
					on:mousedown={(e) => handleSegmentMouseDown(e, seg.id)}
					role="button"
					tabindex="0"
					on:keydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { selectedId = seg.id; e.preventDefault(); } }}
					aria-label="Segment {colorIdx + 1}: {seg.text || 'empty'}"
					aria-pressed={isSelected}
					title="{seg.start.toFixed(2)}s – {seg.end.toFixed(2)}s"
				>
					<!-- Left trim handle -->
					<!-- svelte-ignore a11y-no-static-element-interactions -->
					<div
						class="pt-trim pt-trim--left"
						on:mousedown={(e) => handleLeftTrimMouseDown(e, seg.id)}
						role="separator"
						tabindex="-1"
						aria-label="Trim start"
					></div>

					<!-- Content -->
					<div class="pt-clip-inner">
						<span class="pt-clip-label">{labelText}</span>
						<span class="pt-clip-dur">{(seg.end - seg.start).toFixed(1)}s</span>
					</div>

					<!-- Right trim handle -->
					<!-- svelte-ignore a11y-no-static-element-interactions -->
					<div
						class="pt-trim pt-trim--right"
						on:mousedown={(e) => handleRightTrimMouseDown(e, seg.id)}
						role="separator"
						tabindex="-1"
						aria-label="Trim end"
					></div>

					<!-- Selected indicator dot -->
					{#if isSelected}
						<div class="pt-clip-selected-ring" style="border-color: {color.border};"></div>
					{/if}
				</div>
			{/each}

			{#if tv.segments.length === 0}
				<div class="pt-empty-hint">Click "Add Segment" to create a prompt window</div>
			{/if}
		</div>
	</div>

	<!-- Inline editor for selected segment -->
	{#if selectedSegment}
		{@const colorIdx = segmentColorIndexById.get(selectedSegment.id) ?? -1}
		<div class="pt-editor" style="border-color: rgb(var(--viz-{vizSlot(colorIdx)}) / 0.19);">
			<div class="pt-editor-header">
				<span class="pt-editor-title">
					Segment {colorIdx + 1}
					<span class="pt-editor-time">
						{selectedSegment.start.toFixed(2)}s – {selectedSegment.end.toFixed(2)}s
						({(selectedSegment.end - selectedSegment.start).toFixed(2)}s)
					</span>
				</span>
				<button
					type="button"
					class="pt-delete-btn"
					on:click={deleteSelected}
					aria-label="Delete segment"
					title="Delete segment"
				>
					<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
					</svg>
					Delete
				</button>
			</div>
			<textarea
				class="pt-textarea"
				rows={3}
				placeholder="Enter the prompt that controls this time window…"
				value={selectedSegment.text}
				on:input={(e) => updateText(selectedSegment!.id, (e.target as HTMLTextAreaElement).value)}
				aria-label="Prompt text for segment {colorIdx + 1}"
			></textarea>
		</div>
	{/if}
</div>

<style>
	/* ── Layout ──────────────────────────────────────────────────────────────── */
	.pt-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		flex-wrap: wrap;
		gap: 8px;
		margin-bottom: 8px;
	}

	.pt-meta {
		font-size: 11px;
		color: rgb(var(--fg-subtle));
	}

	.pt-toolbar-right {
		display: flex;
		align-items: center;
		gap: 10px;
	}

	/* ── Zoom controls ───────────────────────────────────────────────────────── */
	.pt-zoom {
		display: flex;
		align-items: center;
		gap: 4px;
	}

	.pt-zoom-btn {
		width: 22px;
		height: 22px;
		background: rgb(var(--surface-2));
		border: 1px solid rgb(var(--line-strong));
		border-radius: 4px;
		color: rgb(var(--fg-muted));
		cursor: pointer;
		display: flex;
		align-items: center;
		justify-content: center;
		font-size: 14px;
		font-weight: 700;
		line-height: 1;
		transition: background 0.1s;
	}

	.pt-zoom-btn:hover {
		background: rgb(var(--surface-3));
	}

	.pt-zoom-label {
		font-size: 10px;
		color: rgb(var(--fg-subtle));
		min-width: 48px;
		text-align: center;
	}

	/* ── Add button ─────────────────────────────────────────────────────────── */
	.pt-add-btn {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		padding: 4px 10px;
		background: rgb(var(--surface-2));
		border: 1px solid rgb(var(--line-strong));
		border-radius: 5px;
		color: rgb(var(--fg-muted));
		font-size: 12px;
		font-weight: 500;
		cursor: pointer;
		transition: background 0.1s, border-color 0.1s;
	}

	.pt-add-btn:hover {
		background: rgb(var(--surface-3));
		border-color: rgb(var(--line-hover));
	}

	/* ── Viewport ────────────────────────────────────────────────────────────── */
	.pt-viewport {
		overflow-x: auto;
		overflow-y: hidden;
		border: 1px solid rgb(var(--surface-2));
		border-radius: 6px;
		background: rgb(var(--surface-1));
	}

	/* ── Ruler ───────────────────────────────────────────────────────────────── */
	.pt-ruler {
		position: relative;
		height: 26px;
		background: rgb(var(--surface-1));
		border-bottom: 1px solid rgb(var(--line-strong));
		min-width: 100%;
	}

	.pt-tick {
		position: absolute;
		top: 0;
		height: 100%;
	}

	.pt-tick-label {
		position: absolute;
		top: 3px;
		left: 4px;
		font-size: 9px;
		color: rgb(var(--fg-subtle));
		white-space: nowrap;
		pointer-events: none;
	}

	.pt-tick-line {
		position: absolute;
		bottom: 0;
		left: 0;
		width: 1px;
		height: 7px;
		background: rgb(var(--line-strong));
	}

	.pt-end-marker {
		position: absolute;
		top: 0;
		bottom: 0;
		width: 2px;
		background: rgb(var(--line-hover));
		cursor: default;
	}

	/* ── Track lane ─────────────────────────────────────────────────────────── */
	.pt-track {
		position: relative;
		height: 56px;
		min-width: 100%;
		cursor: default;
	}

	.pt-empty-hint {
		position: absolute;
		inset: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		font-size: 12px;
		color: rgb(var(--line-hover));
		pointer-events: none;
	}

	/* ── Clips ───────────────────────────────────────────────────────────────── */
	.pt-clip {
		position: absolute;
		top: 4px;
		bottom: 4px;
		border: 2px solid rgb(var(--line-strong));
		border-radius: 5px;
		cursor: grab;
		user-select: none;
		display: flex;
		align-items: stretch;
		overflow: hidden;
		transition: border-color 0.1s;
		min-width: 4px;
	}

	.pt-clip:active {
		cursor: grabbing;
	}

	.pt-clip--selected {
		box-shadow: 0 0 0 1px currentColor;
	}

	.pt-clip-selected-ring {
		position: absolute;
		inset: -1px;
		border-radius: 6px;
		border: 2px solid;
		pointer-events: none;
	}

	/* ── Trim handles ────────────────────────────────────────────────────────── */
	.pt-trim {
		width: 6px;
		flex-shrink: 0;
		cursor: col-resize;
		background: rgba(255, 255, 255, 0.04);
		transition: background 0.1s;
	}

	.pt-trim:hover {
		background: rgba(255, 255, 255, 0.15);
	}

	.pt-trim--left {
		border-radius: 3px 0 0 3px;
	}

	.pt-trim--right {
		border-radius: 0 3px 3px 0;
	}

	/* ── Clip inner content ──────────────────────────────────────────────────── */
	.pt-clip-inner {
		flex: 1;
		display: flex;
		align-items: center;
		gap: 5px;
		padding: 0 4px;
		overflow: hidden;
		min-width: 0;
		pointer-events: none;
		color: rgb(var(--fg));
	}

	.pt-clip-label {
		font-size: 10px;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		flex: 1;
		min-width: 0;
	}

	.pt-clip-dur {
		font-size: 9px;
		color: rgb(var(--fg-subtle));
		white-space: nowrap;
		flex-shrink: 0;
	}

	/* ── Inline editor ───────────────────────────────────────────────────────── */
	.pt-editor {
		margin-top: 10px;
		border: 1px solid rgb(var(--surface-2));
		border-radius: 6px;
		padding: 10px;
		background: rgb(var(--surface-1));
	}

	.pt-editor-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 8px;
	}

	.pt-editor-title {
		font-size: 12px;
		font-weight: 600;
		display: flex;
		align-items: center;
		gap: 8px;
		color: rgb(var(--fg));
	}

	.pt-editor-time {
		font-size: 11px;
		font-weight: 400;
		color: rgb(var(--fg-subtle));
	}

	.pt-delete-btn {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		padding: 3px 8px;
		background: transparent;
		border: 1px solid rgb(var(--line-strong));
		border-radius: 4px;
		color: rgb(var(--danger));
		font-size: 11px;
		cursor: pointer;
		transition: background 0.1s, border-color 0.1s;
	}

	.pt-delete-btn:hover {
		background: rgb(var(--danger) / 0.12);
		border-color: rgb(var(--danger));
	}

	.pt-textarea {
		width: 100%;
		resize: vertical;
		background: rgb(var(--surface-1));
		border: 1px solid rgb(var(--line-strong));
		border-radius: 4px;
		color: rgb(var(--fg-muted));
		font-size: 12px;
		line-height: 1.5;
		padding: 8px 10px;
		outline: none;
		transition: border-color 0.15s;
		font-family: inherit;
	}

	.pt-textarea:focus {
		border-color: rgb(var(--fg-subtle));
	}

	.pt-textarea::placeholder {
		color: rgb(var(--line-hover));
	}
</style>
