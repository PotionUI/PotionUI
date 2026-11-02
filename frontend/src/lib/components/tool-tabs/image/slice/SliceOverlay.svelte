<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import CanvasChip from '$lib/components/tool-tabs/frame/CanvasChip.svelte';
	import { Button } from '$lib/components/ui';
	import type { Analysis, Rect } from '$lib/tool-tabs/image/slice/detect';
	import {
		addCell,
		deleteCell,
		deriveLines,
		dragLine,
		lineKey,
		mergeCells,
		numbering,
		resizeCell,
		splitCell,
		toggleSkip,
		type Cell,
		type Line
	} from '$lib/tool-tabs/image/slice/model';

	type CellState = 'done' | 'saving' | 'queued';

	let {
		cells,
		an,
		size,
		selected = [],
		snap = true,
		phone = false,
		locked = false,
		adding = false,
		padding = null,
		states = null,
		loupeSrc = null,
		onchange,
		onselect,
		onadded
	}: {
		cells: Cell[];
		an: Analysis | null;
		size: { width: number; height: number };
		selected?: string[];
		snap?: boolean;
		phone?: boolean;
		locked?: boolean;
		adding?: boolean;
		padding?: Map<string, Rect> | null;
		states?: Map<string, CellState> | null;
		loupeSrc?: string | null;
		onchange: (cells: Cell[], label?: string) => void;
		onselect: (ids: string[]) => void;
		onadded?: () => void;
	} = $props();

	let box = $state<HTMLElement>();
	let hotLine = $state<string | null>(null);
	let freeDrag = $state(false);
	let draft = $state<Rect | null>(null);
	let loupe = $state<{ x: number; y: number } | null>(null);

	let lines = $derived(deriveLines(cells, an));
	let numbers = $derived(numbering(cells));
	let selectedCell = $derived(selected.length === 1 ? (cells.find((cell) => cell.id === selected[0]) ?? null) : null);
	let hot = $derived(hotLine ? (lines.find((line) => line.id === hotLine) ?? null) : null);
	let scrim = $derived.by(() => {
		const holes = cells.filter((cell) => !cell.skip).map((cell) => `M${cell.x0} ${cell.y0}V${cell.y1}H${cell.x1}V${cell.y0}Z`);
		return ['M0 0H1V1H0Z', ...holes].join(' ');
	});

	const pct = (n: number) => `${n * 100}%`;
	const place = (r: Rect) => `left:${pct(r.x0)};top:${pct(r.y0)};width:${pct(r.x1 - r.x0)};height:${pct(r.y1 - r.y0)}`;
	const sideOf = (line: Pick<Line, 'axis'>) => (line.axis === 'v' ? size.width : size.height);
	const segRect = (line: Line, lo: number, hi: number): Rect =>
		line.axis === 'v' ? { x0: line.a, x1: line.b, y0: lo, y1: hi } : { y0: line.a, y1: line.b, x0: lo, x1: hi };

	function knob(line: Line) {
		const clean = line.segments.filter((segment) => segment.clean);
		const seg = (clean.length ? clean : line.segments).reduce((a, b) => (b.hi - b.lo > a.hi - a.lo ? b : a));
		return { at: (line.a + line.b) / 2, mid: (seg.lo + seg.hi) / 2 };
	}

	function lineLabel(line: Line) {
		const pair = line.pairs[0].map((id) => numbers.get(id) ?? '—');
		return `Line between cells ${pair[0]} and ${pair[1]}`;
	}

	function local(event: { clientX: number; clientY: number }) {
		const rect = box!.getBoundingClientRect();
		return {
			x: Math.min(1, Math.max(0, (event.clientX - rect.left) / Math.max(1, rect.width))),
			y: Math.min(1, Math.max(0, (event.clientY - rect.top) / Math.max(1, rect.height))),
			rect
		};
	}

	function track(onmove: (event: PointerEvent) => void, onend?: () => void) {
		const up = () => {
			window.removeEventListener('pointermove', onmove);
			window.removeEventListener('pointerup', up);
			window.removeEventListener('pointercancel', up);
			onend?.();
		};
		window.addEventListener('pointermove', onmove);
		window.addEventListener('pointerup', up);
		window.addEventListener('pointercancel', up);
	}

	function startLine(event: PointerEvent, line: Line) {
		if (locked || event.button !== 0 || !box) return;
		event.preventDefault();
		event.stopPropagation();
		const touch = event.pointerType === 'touch' || phone;
		const rect = box.getBoundingClientRect();
		const radius = (touch ? 24 : 12) / Math.max(1, line.axis === 'v' ? rect.width : rect.height);
		const start = cells;
		let first = true;
		hotLine = line.id;
		const move = (next: PointerEvent) => {
			const point = local(next);
			const pos = line.axis === 'v' ? point.x : point.y;
			const snapping = touch ? snap : snap !== next.altKey;
			const result = dragLine(an, start, line, pos, radius, snapping);
			freeDrag = !result.snapped;
			hotLine = lineKey(line.axis, result.a, result.b);
			loupe = touch ? { x: point.x, y: point.y } : null;
			onchange(result.cells, first ? 'line drag' : undefined);
			first = false;
		};
		track(move, () => {
			hotLine = null;
			freeDrag = false;
			loupe = null;
		});
	}

	function nudge(event: KeyboardEvent, line: Line) {
		const forward = line.axis === 'v' ? 'ArrowRight' : 'ArrowDown';
		const back = line.axis === 'v' ? 'ArrowLeft' : 'ArrowUp';
		if (locked || (event.key !== forward && event.key !== back)) return;
		event.preventDefault();
		event.stopPropagation();
		const step = ((event.shiftKey ? 10 : 1) / Math.max(1, sideOf(line))) * (event.key === forward ? 1 : -1);
		const pos = (line.a + line.b) / 2 + step;
		const result = dragLine(an, cells, line, pos, Math.abs(step) * 0.6, snap && !event.altKey);
		const moved = result.snapped && Math.abs((result.a + result.b) / 2 - (line.a + line.b) / 2) < 1e-6 ? dragLine(an, cells, line, pos, 0, false) : result;
		onchange(moved.cells, 'line nudge');
		hotLine = null;
	}

	let press: { id: string; timer: ReturnType<typeof setTimeout>; long: boolean } | null = null;

	function cellDown(event: PointerEvent, cell: Cell) {
		if (locked || event.pointerType !== 'touch') return;
		const entry = { id: cell.id, long: false, timer: setTimeout(() => {
			entry.long = true;
			if (selected.length && !selected.includes(cell.id)) onselect([...selected, cell.id]);
		}, 450) };
		press = entry;
	}

	function cellClick(event: MouseEvent, cell: Cell) {
		if (locked) return;
		if (press?.id === cell.id) {
			clearTimeout(press.timer);
			const long = press.long;
			press = null;
			if (long) return;
		}
		onselect(event.shiftKey ? [...selected.filter((id) => id !== cell.id), cell.id] : [cell.id]);
	}

	function mergePair(pair: [string, string]) {
		onchange(mergeCells(cells, pair), 'merge');
		onselect([pair[0]]);
	}

	export function act(kind: 'split' | 'skip' | 'delete' | 'merge') {
		if (locked) return;
		if (kind === 'merge') {
			if (selected.length < 2) return;
			onchange(mergeCells(cells, selected), 'merge');
			onselect([selected[0]]);
			return;
		}
		const cell = selectedCell;
		if (!cell) return;
		if (kind === 'split') {
			onchange(splitCell(cells, cell.id, an), 'split');
			onselect([]);
		} else if (kind === 'skip') onchange(toggleSkip(cells, cell.id), cell.skip ? 'keep' : 'skip');
		else {
			onchange(deleteCell(cells, cell.id), 'delete');
			onselect([]);
		}
	}

	function cellKey(event: KeyboardEvent, cell: Cell) {
		if (locked) return;
		const key = event.key.toLowerCase();
		if ((event.ctrlKey || event.metaKey) && key === 'm') {
			event.preventDefault();
			act('merge');
			return;
		}
		if (event.ctrlKey || event.metaKey || event.altKey) return;
		if (key === 's') {
			event.preventDefault();
			onchange(toggleSkip(cells, cell.id), cell.skip ? 'keep' : 'skip');
		} else if (key === 'delete' || key === 'backspace') {
			event.preventDefault();
			onchange(deleteCell(cells, cell.id), 'delete');
			onselect([]);
		}
	}

	function startCorner(event: PointerEvent, cell: Cell, corner: 'nw' | 'ne' | 'sw' | 'se') {
		if (locked || event.button !== 0 || !box) return;
		event.preventDefault();
		event.stopPropagation();
		const start = cells;
		let first = true;
		track((next) => {
			const point = local(next);
			const rect: Rect = { ...cell };
			if (corner.includes('w')) rect.x0 = Math.min(point.x, cell.x1 - 0.01);
			if (corner.includes('e')) rect.x1 = Math.max(point.x, cell.x0 + 0.01);
			if (corner.includes('n')) rect.y0 = Math.min(point.y, cell.y1 - 0.01);
			if (corner.includes('s')) rect.y1 = Math.max(point.y, cell.y0 + 0.01);
			onchange(resizeCell(start, cell.id, rect), first ? 'resize' : undefined);
			first = false;
		});
	}

	function startAdd(event: PointerEvent) {
		if (locked || event.button !== 0 || !box) return;
		event.preventDefault();
		event.stopPropagation();
		const origin = local(event);
		draft = { x0: origin.x, y0: origin.y, x1: origin.x, y1: origin.y };
		track(
			(next) => {
				const point = local(next);
				draft = { x0: Math.min(origin.x, point.x), y0: Math.min(origin.y, point.y), x1: Math.max(origin.x, point.x), y1: Math.max(origin.y, point.y) };
			},
			() => {
				if (draft) onchange(addCell(cells, draft), 'add box');
				draft = null;
				onadded?.();
			}
		);
	}

	let barStyle = $derived.by(() => {
		const c = selectedCell ?? (selected.length > 1 ? cells.find((cell) => cell.id === selected[selected.length - 1]) : null);
		if (!c) return null;
		const right = (c.x0 + c.x1) / 2 > 0.5;
		const horizontal = right ? `left:${pct(c.x1)};--tx:-100%` : `left:${pct(c.x0)};--tx:0%`;
		const vertical = c.y0 < 0.12 ? `top:calc(${pct(c.y1)} + 10px);transform:translateX(var(--tx))` : `top:calc(${pct(c.y0)} - 10px);transform:translate(var(--tx), -100%)`;
		return `${horizontal};${vertical}`;
	});
</script>

<div bind:this={box} class="slice-overlay absolute inset-0 {phone ? 'is-phone' : ''}" data-slice-overlay>
	<svg class="pointer-events-none absolute inset-0 h-full w-full" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true">
		<path d={scrim} fill-rule="evenodd" style="fill: rgb(var(--scrim) / {cells.length ? 0.58 : 0})" />
	</svg>

	{#each cells as cell (cell.id)}
		{@const on = selected.includes(cell.id)}
		{@const state = states?.get(cell.id)}
		<button
			type="button"
			class="cell absolute {cell.skip ? 'is-skipped' : ''} {on ? 'is-on' : ''}"
			style={place(cell)}
			aria-pressed={on}
			aria-label={cell.skip ? 'Skipped cell' : `Cell ${numbers.get(cell.id)}`}
			onpointerdown={(event) => cellDown(event, cell)}
			onclick={(event) => cellClick(event, cell)}
			onkeydown={(event) => cellKey(event, cell)}
			data-slice-cell={numbers.get(cell.id) ?? 'skipped'}
		>
			<span class="pointer-events-none absolute left-2 top-2 flex gap-1">
				{#if cell.skip}
					<CanvasChip><span class="inline-flex items-center gap-1"><Icon name="editor-eye-off" className="h-3.5 w-3.5" />Skipped</span></CanvasChip>
				{:else}
					<CanvasChip class={on ? '!bg-signal-solid !text-scrim-on' : ''}>{numbers.get(cell.id)}</CanvasChip>
					{#if cell.merged && !phone}<CanvasChip>Merged</CanvasChip>{/if}
					{#if state === 'done'}
						<CanvasChip class="!bg-success-solid !text-scrim-on"><span class="inline-flex items-center gap-1" aria-label="Saved"><Icon name="check" className="h-3.5 w-3.5" strokeWidth={2.4} />{#if !phone}Saved{/if}</span></CanvasChip>
					{:else if state === 'saving'}
						<CanvasChip><span class="inline-flex items-center gap-1.5" aria-label="Saving"><span class="h-2 w-2 animate-pulse rounded-full bg-signal"></span>{#if !phone}Saving{/if}</span></CanvasChip>
					{/if}
				{/if}
			</span>
		</button>
		{#if padding?.get(cell.id) && !cell.skip}
			<div class="pad pointer-events-none absolute" style={place(padding.get(cell.id)!)} aria-hidden="true" data-slice-pad></div>
		{/if}
	{/each}

	{#each lines as line (line.id)}
		{@const isHot = hotLine === line.id}
		{@const k = knob(line)}
		{#each line.segments as seg, i (i)}
			{@const r = segRect(line, seg.lo, seg.hi)}
			{#if !seg.clean && !isHot}
				<div class="cut pointer-events-none absolute {line.axis}" style={place(line.snapped ? r : { ...r, ...(line.axis === 'v' ? { x0: line.a - 0.002, x1: line.a + 0.002 } : { y0: line.a - 0.002, y1: line.a + 0.002 }) })} aria-hidden="true" data-slice-cut-line></div>
				{#if !phone && !locked}
					{@const pair = line.pairs[i]}
					<div
						class="absolute z-10 flex -translate-x-1/2 -translate-y-1/2 items-center gap-1 rounded bg-danger-solid p-[3px] pl-2 shadow-floating"
						style="left:{pct(line.axis === 'v' ? (line.a + line.b) / 2 : (seg.lo + seg.hi) / 2)};top:{pct(line.axis === 'v' ? (seg.lo + seg.hi) / 2 : (line.a + line.b) / 2)}"
						data-slice-cut
					>
						<span class="inline-flex items-center gap-1.5 whitespace-nowrap font-mono text-sm uppercase tracking-[0.08em] text-scrim-on"><Icon name="warning" className="h-3.5 w-3.5" />Cuts a panel</span>
						<Button variant="primary" size="xs" class="h-6" onclick={() => mergePair(pair)} attrs={{ 'data-slice-cut-merge': '' }}>Merge {numbers.get(pair[0]) ?? '—'} + {numbers.get(pair[1]) ?? '—'}</Button>
					</div>
				{/if}
			{:else if line.snapped}
				<div class="band pointer-events-none absolute {line.axis} {isHot ? 'is-hot' : ''}" style={place(r)} aria-hidden="true" data-slice-band></div>
			{:else}
				<div class="hair pointer-events-none absolute {line.axis} {isHot ? 'is-hot' : ''}" style={place(r)} aria-hidden="true"></div>
			{/if}
		{/each}
		{#if isHot && line.snapped}
			{#each [line.lo, line.hi] as end, i (i)}
				<span
					class="pointer-events-none absolute grid h-5 w-5 place-items-center rounded bg-signal-solid text-scrim-on shadow-floating"
					style={line.axis === 'v'
						? `left:${pct((line.a + line.b) / 2)};top:${pct(end)};transform:translate(-50%, ${i ? '6px' : 'calc(-100% - 6px)'})`
						: `top:${pct((line.a + line.b) / 2)};left:${pct(end)};transform:translate(${i ? '6px' : 'calc(-100% - 6px)'}, -50%)`}
					aria-hidden="true"
					data-slice-magnet
				>
					<Icon name="magnet" className="h-3.5 w-3.5" strokeWidth={2} />
				</span>
			{/each}
		{/if}
		{#if !locked}
			<button
				type="button"
				class="knob absolute {line.axis} {isHot ? 'is-hot' : ''}"
				style={line.axis === 'v' ? `left:${pct(k.at)};top:${pct(k.mid)}` : `left:${pct(k.mid)};top:${pct(k.at)}`}
				aria-label={lineLabel(line)}
				onpointerdown={(event) => startLine(event, line)}
				onkeydown={(event) => nudge(event, line)}
				data-slice-knob={line.axis}
			><span class="grip"></span></button>
		{/if}
		{#if isHot && !phone}
			<div
				class="pointer-events-none absolute z-10"
				style={line.axis === 'v' ? `left:${pct(line.b)};top:${pct(k.mid)};transform:translate(16px, -50%)` : `left:${pct(k.mid)};top:${pct(line.b)};transform:translate(-50%, 12px)`}
				data-slice-drag-chip
			>
				<CanvasChip><span class="inline-flex items-center gap-1.5">{#if line.snapped && !freeDrag}<Icon name="magnet" className="h-3.5 w-3.5 text-signal" />Gutter · {Math.round((line.b - line.a) * sideOf(line))} px{:else}Free · {Math.round(line.a * sideOf(line))} px{/if}</span></CanvasChip>
			</div>
		{/if}
	{/each}

	{#if selectedCell && !locked && !phone}
		{@const c = selectedCell}
		{#each [['nw', c.x0, c.y0], ['ne', c.x1, c.y0], ['sw', c.x0, c.y1], ['se', c.x1, c.y1]] as const as [corner, x, y] (corner)}
			<span
				class="corner absolute h-2.5 w-2.5 rounded-[2px] bg-scrim-on shadow-floating {corner === 'nw' || corner === 'se' ? 'cursor-nwse-resize' : 'cursor-nesw-resize'}"
				style="left:{pct(x)};top:{pct(y)}"
				role="presentation"
				onpointerdown={(event) => startCorner(event, c, corner)}
				data-slice-corner={corner}
			></span>
		{/each}
	{/if}

	{#if barStyle && !locked && !phone}
		<div class="absolute z-20 flex gap-0.5 rounded-lg border border-scrim-on/10 bg-scrim/75 p-[3px] text-scrim-on shadow-floating backdrop-blur" style={barStyle} role="toolbar" aria-label="Cell actions" data-slice-cellbar>
			<Button variant="overlay" size="xs" class="h-7" icon="cell-split" disabled={!selectedCell} onclick={() => act('split')} attrs={{ 'data-cell-action': 'split' }}>Split</Button>
			<Button variant="overlay" size="xs" class="h-7" icon="cell-merge" disabled={selected.length < 2} onclick={() => act('merge')} attrs={{ 'data-cell-action': 'merge' }}>Merge</Button>
			<Button variant="overlay" size="xs" class="h-7" icon={selectedCell?.skip ? 'editor-eye' : 'editor-eye-off'} disabled={!selectedCell} onclick={() => act('skip')} attrs={{ 'data-cell-action': 'skip' }}>{selectedCell?.skip ? 'Keep' : 'Skip'}</Button>
			<Button variant="overlay" size="xs" class="h-7" icon="trash" disabled={!selectedCell} onclick={() => act('delete')} attrs={{ 'data-cell-action': 'delete' }}>Delete</Button>
		</div>
	{/if}

	{#if adding && !locked}
		<div class="absolute inset-0 z-30 cursor-crosshair" role="presentation" onpointerdown={startAdd} data-slice-add-surface>
			{#if draft}
				<div class="draft pointer-events-none absolute" style={place(draft)}></div>
			{/if}
		</div>
	{/if}

	{#if loupe && hot}
		{@const cx = (hot.a + hot.b) / 2}
		{@const cy = hot.axis === 'v' ? loupe.y : cx}
		{@const lx = hot.axis === 'v' ? cx : loupe.x}
		<span class="touch pointer-events-none absolute" style="left:{pct(lx)};top:{pct(cy)}" aria-hidden="true"></span>
		<div class="loupe pointer-events-none absolute z-30" style="left:{pct(lx)};top:{pct(cy)}" aria-hidden="true" data-slice-loupe>
			<div
				class="lens"
				style="{loupeSrc ? `background-image:url(${loupeSrc});` : ''}background-size:{(box?.clientWidth ?? 0) * 3}px {(box?.clientHeight ?? 0) * 3}px;background-position:{56 - lx * (box?.clientWidth ?? 0) * 3}px {56 - cy * (box?.clientHeight ?? 0) * 3}px"
			>
				{#if hot.snapped}
					<div class="lens-band {hot.axis}" style={hot.axis === 'v' ? `width:${(hot.b - hot.a) * (box?.clientWidth ?? 0) * 3}px` : `height:${(hot.b - hot.a) * (box?.clientHeight ?? 0) * 3}px`}></div>
				{/if}
			</div>
			<div class="mt-1.5 flex justify-center">
				<CanvasChip><span class="inline-flex items-center gap-1">{#if hot.snapped}<Icon name="magnet" className="h-3.5 w-3.5 text-signal" />{Math.round((hot.b - hot.a) * sideOf(hot))} px{:else}Free{/if}</span></CanvasChip>
			</div>
		</div>
	{/if}
</div>

<style>
	.cell {
		outline: 1px solid rgb(var(--on-scrim) / 0.92);
		box-shadow: 0 0 0 1px rgb(var(--scrim) / 0.45), inset 0 0 0 1px rgb(var(--scrim) / 0.25);
		cursor: pointer;
	}
	.cell:focus-visible {
		outline: 2px solid rgb(var(--signal));
	}
	.cell.is-on {
		outline: 2px solid rgb(var(--signal));
		box-shadow: 0 0 0 1px rgb(var(--scrim) / 0.5);
	}
	.cell.is-skipped {
		outline: 1px dashed rgb(var(--on-scrim) / 0.6);
		background: repeating-linear-gradient(135deg, rgb(var(--scrim) / 0.62) 0 8px, rgb(var(--scrim) / 0.44) 8px 16px);
	}
	.pad {
		outline: 1px dashed rgb(var(--on-scrim) / 0.75);
		box-shadow: 0 0 0 1px rgb(var(--scrim) / 0.35);
	}
	.draft {
		outline: 1px solid rgb(var(--on-scrim));
		background: rgb(var(--signal) / 0.12);
	}
	.band {
		background: rgb(var(--signal) / 0.16);
		box-shadow: inset 0 0 0 1px rgb(var(--signal) / 0.55);
	}
	.band.is-hot {
		background: rgb(var(--signal) / 0.38);
		box-shadow: inset 0 0 0 1px rgb(var(--signal)), 0 0 0 1px rgb(var(--scrim) / 0.4);
	}
	.hair.v {
		border-left: 2px solid rgb(var(--on-scrim));
		box-shadow: -1px 0 0 rgb(var(--scrim) / 0.5);
		transform: translateX(-1px);
	}
	.hair.h {
		border-top: 2px solid rgb(var(--on-scrim));
		box-shadow: 0 -1px 0 rgb(var(--scrim) / 0.5);
		transform: translateY(-1px);
	}
	.cut {
		background: repeating-linear-gradient(0deg, rgb(var(--danger-solid) / 0.75) 0 6px, transparent 6px 11px);
	}
	.cut.h {
		background: repeating-linear-gradient(90deg, rgb(var(--danger-solid) / 0.75) 0 6px, transparent 6px 11px);
	}
	.knob {
		display: grid;
		place-items: center;
		width: 22px;
		height: 34px;
		transform: translate(-50%, -50%);
		cursor: ew-resize;
		touch-action: none;
	}
	.knob.h {
		width: 34px;
		height: 22px;
		cursor: ns-resize;
	}
	.knob .grip {
		width: 6px;
		height: 24px;
		border-radius: 2px;
		background: rgb(var(--on-scrim));
		box-shadow: 0 0 0 1px rgb(var(--scrim) / 0.45), 0 2px 6px rgb(var(--scrim) / 0.4);
	}
	.knob.h .grip {
		width: 24px;
		height: 6px;
	}
	.knob.is-hot .grip {
		box-shadow: 0 0 0 2px rgb(var(--signal)), 0 2px 8px rgb(var(--scrim) / 0.5);
	}
	.knob:focus-visible {
		outline: none;
	}
	.knob:focus-visible .grip {
		box-shadow: 0 0 0 2px rgb(var(--signal));
	}
	.is-phone .knob {
		width: 44px;
		height: 44px;
	}
	.is-phone .knob .grip {
		width: 8px;
		height: 30px;
	}
	.is-phone .knob.h .grip {
		width: 30px;
		height: 8px;
	}
	.corner {
		transform: translate(-50%, -50%);
		touch-action: none;
	}
	.touch {
		width: 44px;
		height: 44px;
		border-radius: 9999px;
		transform: translate(-50%, -50%);
		background: rgb(var(--on-scrim) / 0.18);
		box-shadow: 0 0 0 2px rgb(var(--on-scrim) / 0.55);
	}
	.loupe {
		transform: translate(-50%, calc(-100% - 64px));
	}
	.lens {
		position: relative;
		width: 112px;
		height: 112px;
		overflow: hidden;
		border-radius: 10px;
		background-color: rgb(var(--scrim));
		background-repeat: no-repeat;
		box-shadow: 0 0 0 2px rgb(var(--on-scrim)), 0 8px 24px rgb(var(--scrim) / 0.55);
	}
	.lens-band {
		position: absolute;
		background: rgb(var(--signal) / 0.32);
		box-shadow: inset 0 0 0 1px rgb(var(--signal));
	}
	.lens-band.v {
		top: 0;
		bottom: 0;
		left: 50%;
		transform: translateX(-50%);
	}
	.lens-band.h {
		left: 0;
		right: 0;
		top: 50%;
		transform: translateY(-50%);
	}
</style>
