<script lang="ts" module>
	export interface NameRow {
		id: string;
		n: string;
		name: string;
		typed: boolean;
		thumb: string | null;
		skip: boolean;
	}
</script>

<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import Icon from '$lib/components/Icon.svelte';
	import EntryForm from '$lib/components/tool-tabs/frame/EntryForm.svelte';
	import { Button, Input, Switch } from '$lib/components/ui';
	import { SLICE_NO_COLLECTION, clampInt, sliceCellsFields, sliceGridFields, sliceNameFields, sliceSaveFields, sliceValues } from '$lib/tool-tabs/image/optionSchemas';
	import { GRID_PRESETS, MAX_GRID, PATTERN_TOKENS, type SliceAspect, type SliceParams } from '$lib/tool-tabs/image/slice/model';

	let {
		params,
		names = [],
		collections = [],
		fill = null,
		transparent = false,
		phone = false,
		locked = false,
		adding = false,
		selected = [],
		selectedNumber = null,
		overlap = false,
		tab = $bindable('layout'),
		onpreset,
		ondetect,
		onsnap,
		onoutput,
		onpattern,
		onrename,
		onskip,
		oncollection,
		onadd,
		onclear,
		oncell
	}: {
		params: SliceParams;
		names?: NameRow[];
		collections?: { value: string; label: string }[];
		fill?: { hex: string; rgb: string } | null;
		transparent?: boolean;
		phone?: boolean;
		locked?: boolean;
		adding?: boolean;
		selected?: string[];
		selectedNumber?: string | null;
		overlap?: boolean;
		tab?: 'layout' | 'output';
		onpreset: (cols: number, rows: number) => void;
		ondetect: () => void;
		onsnap: (on: boolean) => void;
		onoutput: (patch: Partial<SliceParams['output']>) => void;
		onpattern: (pattern: string) => void;
		onrename: (id: string, name: string) => void;
		onskip: (id: string) => void;
		oncollection: (id: string | null) => void;
		onadd: () => void;
		onclear: () => void;
		oncell: (kind: 'split' | 'skip' | 'delete' | 'merge') => void;
	} = $props();

	let namesSection = $state<HTMLElement | null>(null);
	let values = $derived(sliceValues(params));
	let gridFields = $derived(sliceGridFields(locked));
	let cellsFields = $derived(sliceCellsFields(locked));
	let nameFields = $derived(sliceNameFields(locked));
	let saveFields = $derived(sliceSaveFields(collections, locked));
	let cellCount = $derived(params.cells.length);
	let keptCount = $derived(params.cells.filter((cell) => !cell.skip).length);
	let current = $derived(cellCount ? tab : 'layout');
	let selectedSkip = $derived(selected.length === 1 ? !!params.cells.find((cell) => cell.id === selected[0])?.skip : false);

	const chip =
		'rounded border font-mono text-sm tabular-nums transition-colors focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-1 focus-visible:outline-signal disabled:cursor-not-allowed disabled:opacity-50';
	const on = 'border-signal/60 bg-signal/10 text-signal';
	const off = 'border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg';
	const head = 'font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle';

	function writeGrid(next: Record<string, unknown>, info: { commit: boolean; name?: string }) {
		if (info.name === 'snap') {
			if (!info.commit) onsnap(next.snap === true);
			return;
		}
		if (!info.commit) return;
		const value = clampInt(next[info.name ?? ''], 1, MAX_GRID);
		if (value === null) return;
		if (info.name === 'cols') onpreset(value, params.layout.rows);
		else if (info.name === 'rows') onpreset(params.layout.cols, value);
	}

	function writeCells(next: Record<string, unknown>, info: { commit: boolean; name?: string }) {
		const name = info.name;
		if (name === 'trim') {
			if (!info.commit) onoutput({ trim: next.trim === true });
		} else if (name === 'aspect') {
			if (!info.commit) onoutput({ aspect: next.aspect as SliceAspect });
		} else if (info.commit && name === 'tolerance') {
			const value = clampInt(next.tolerance, 0, 64);
			if (value !== null) onoutput({ tolerance: value });
		} else if (info.commit && name === 'padding') {
			const value = clampInt(next.padding, 0, 256);
			if (value !== null) onoutput({ padding: value });
		}
	}

	function writeName(next: Record<string, unknown>, info: { commit: boolean }) {
		if (!info.commit) onpattern(String(next.pattern ?? ''));
	}

	function writeSave(next: Record<string, unknown>, info: { commit: boolean }) {
		if (info.commit) return;
		oncollection(next.collection && next.collection !== SLICE_NO_COLLECTION ? String(next.collection) : null);
	}

	function insertToken(token: string) {
		const el = namesSection?.querySelector<HTMLInputElement>('[data-field-name="pattern"] input') ?? null;
		const value = params.pattern;
		const start = el?.selectionStart ?? value.length;
		const end = el?.selectionEnd ?? value.length;
		onpattern(value.slice(0, start) + token + value.slice(end));
	}
</script>

{#snippet glyph(c: number, r: number)}
	<svg viewBox="0 0 24 16" class="h-4 w-6 flex-none" aria-hidden="true">
		{#each Array.from({ length: r }) as _row, y (y)}
			{#each Array.from({ length: c }) as _col, x (x)}
				<rect x={0.75 + x * (22.5 / c)} y={0.75 + y * (14.5 / r)} width={22.5 / c - 1.5} height={14.5 / r - 1.5} rx="0.8" fill="none" stroke="currentColor" stroke-width="1.2" />
			{/each}
		{/each}
	</svg>
{/snippet}

{#if phone}
	{#if selected.length && !locked}
		<div class="flex items-center gap-1.5" role="toolbar" aria-label="Cell {selectedNumber ?? ''}" data-slice-phone-cell>
			<span class="mr-1 whitespace-nowrap font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">{selected.length > 1 ? `${selected.length} cells` : `Cell ${selectedNumber ?? '—'}`}</span>
			{#if selected.length > 1}
				<Button variant="secondary" size="sm" class="h-9 flex-1 justify-center" icon="cell-merge" onclick={() => oncell('merge')}>Merge</Button>
			{:else}
				<Button variant="secondary" size="sm" class="h-9 flex-1 justify-center" icon="cell-split" onclick={() => oncell('split')}>Split</Button>
				<Button variant="secondary" size="sm" class="h-9 flex-1 justify-center" icon={selectedSkip ? 'editor-eye' : 'editor-eye-off'} onclick={() => oncell('skip')}>{selectedSkip ? 'Keep' : 'Skip'}</Button>
				<Button variant="secondary" size="sm" class="h-9 flex-1 justify-center" icon="trash" onclick={() => oncell('delete')}>Delete</Button>
			{/if}
		</div>
		<p class="text-sm text-fg-muted">Long-press a second cell to merge. Drag its lines to resize.</p>
	{:else}
		<div class="flex gap-1.5 overflow-x-auto" style="scrollbar-width:none" role="group" aria-label="Layout" data-slice-phone-layout>
			<button type="button" class="{chip} inline-flex h-9 flex-none items-center gap-1.5 px-3 {off}" disabled={locked} onclick={() => ondetect()} data-slice-detect>
				<Icon name="scan-search" className="h-4 w-4" />Detect
			</button>
			{#each GRID_PRESETS as p (p.key)}
				<button
					type="button"
					class="{chip} inline-flex h-9 flex-none items-center gap-1.5 px-2.5 {params.layout.preset === p.key ? on : off}"
					aria-pressed={params.layout.preset === p.key}
					aria-label="{p.cols} across, {p.rows} down"
					disabled={locked}
					onclick={() => onpreset(p.cols, p.rows)}
					data-slice-preset={p.key}
				>
					{@render glyph(p.cols, p.rows)}{p.cols}×{p.rows}
				</button>
			{/each}
		</div>
		<div class="flex items-center gap-3 text-sm">
			<span class="flex items-center gap-2 text-fg-muted"><Switch checked={params.layout.snap} size="sm" label="Snap to gutters" onchange={(value) => onsnap(value)} />Snap</span>
			<span class="flex items-center gap-2 text-fg-muted"><Switch checked={params.output.trim} size="sm" label="Trim background" onchange={(value) => onoutput({ trim: value })} />Trim</span>
			<span class="flex-1"></span>
			{#if cellCount}<span class="font-mono tabular-nums text-fg-subtle" data-slice-kept>{keptCount} of {cellCount} kept</span>{/if}
		</div>
	{/if}
{:else}
	{#if cellCount}
		<div class="flex items-center gap-4 border-b border-line" role="tablist" aria-label="Slice">
			{#each [['layout', 'Layout', ''], ['output', 'Output', String(keptCount)]] as [id, label, n] (id)}
				<button
					type="button"
					role="tab"
					aria-selected={current === id}
					class="-mb-px border-b-2 px-1 pb-2 text-base {current === id ? 'border-fg text-fg' : 'border-transparent text-fg-muted hover:text-fg'}"
					onclick={() => (tab = id as 'layout' | 'output')}
					data-slice-tab={id}
				>{label} {#if n}<span class="font-mono text-sm tabular-nums text-fg-subtle">{n}</span>{/if}</button>
			{/each}
		</div>
	{/if}
	{#if current === 'layout'}
		<section class="flex flex-col gap-2.5" data-slice-section="layout">
			<Button variant="secondary" size="sm" class="h-8 w-full justify-center" icon="scan-search" disabled={locked} onclick={() => ondetect()} attrs={{ 'data-slice-detect': '' }}>
				{params.layout.detected ? 'Detect again' : 'Detect panels'}
			</Button>
			{#if params.layout.detected}
				<p class="flex items-start gap-1.5 text-sm text-fg-muted" data-slice-detected>
					<Icon name="check" className="mt-0.5 h-3.5 w-3.5 flex-none text-success" />
					<span>{params.layout.detected}</span>
				</p>
			{/if}
			<div class="flex items-center gap-2 pt-1">
				<span class="h-px flex-1 bg-line"></span>
				<span class="text-sm text-fg-subtle">or a grid, columns × rows</span>
				<span class="h-px flex-1 bg-line"></span>
			</div>
			<div class="grid grid-cols-5 gap-1.5" role="group" aria-label="Grid presets, columns × rows">
				{#each GRID_PRESETS as p (p.key)}
					<button
						type="button"
						class="{chip} flex h-12 flex-col items-center justify-center gap-1 {params.layout.preset === p.key ? on : off}"
						aria-pressed={params.layout.preset === p.key}
						aria-label="{p.cols} across, {p.rows} down"
						disabled={locked}
						onclick={() => onpreset(p.cols, p.rows)}
						data-slice-preset={p.key}
					>
						{@render glyph(p.cols, p.rows)}
						<span>{p.cols}×{p.rows}</span>
					</button>
				{/each}
			</div>
			<EntryForm fields={gridFields} {values} label="Grid" onValues={writeGrid} />
			<div class="flex gap-1.5">
				<Button variant="ghost" size="sm" class="h-7 flex-1 justify-center" icon="plus" pressed={adding} disabled={locked} onclick={() => onadd()} attrs={{ 'data-slice-add': '' }}>Add box</Button>
				<Button variant="ghost" size="sm" class="h-7 flex-1 justify-center" icon="refresh" disabled={!cellCount || locked} onclick={() => onclear()} attrs={{ 'data-slice-clear': '' }}>Clear</Button>
			</div>
			{#if overlap}
				<p class="text-sm text-warning" data-slice-overlap>Two cells overlap by more than half. That is usually a mistake.</p>
			{/if}
		</section>
	{:else}
		<section class="flex flex-col gap-3" data-slice-section="cells">
			<div class="flex items-baseline gap-2">
				<p class={head}>Every cell</p>
				<span class="flex-1"></span>
				<span class="font-mono text-sm tabular-nums text-fg-subtle">{keptCount} of {cellCount} kept</span>
			</div>
			<EntryForm fields={cellsFields} {values} label="Every cell" onValues={writeCells} />
			<p class="flex items-center gap-1.5 text-sm text-fg-muted" data-slice-fill>
				{#if transparent}
					<span class="swatch-checker h-3.5 w-3.5 flex-none rounded-sm border border-line-strong" aria-hidden="true"></span>
					Padding stays transparent
				{:else}
					<span class="h-3.5 w-3.5 flex-none rounded-sm border border-line-strong" style={fill ? `background:rgb(${fill.rgb})` : ''} aria-hidden="true"></span>
					Padding fills with the sheet colour {#if fill}<span class="font-mono text-fg-subtle">{fill.hex}</span>{/if}
				{/if}
			</p>
		</section>

		<div class="h-px bg-line"></div>

		<section class="flex flex-col gap-2" data-slice-section="names" bind:this={namesSection}>
			<p class={head}>Names</p>
			<EntryForm fields={nameFields} {values} label="Names" onValues={writeName} />
			<div class="flex flex-wrap items-center gap-1" role="group" aria-label="Insert a token">
				{#each PATTERN_TOKENS as token (token)}
					<button type="button" class="{chip} h-6 px-1.5 {off}" disabled={locked} onclick={() => insertToken(token)}>{token}</button>
				{/each}
			</div>
			<ol class="flex flex-col" aria-label="Output names" data-slice-names>
				{#each names as row (row.id)}
					{@const isOn = selected.includes(row.id)}
					<li class="flex h-10 items-center gap-2 {isOn ? 'rounded bg-signal/[0.08]' : ''}" data-slice-name-row={row.skip ? 'skipped' : row.n}>
						<span class="w-[18px] flex-none font-mono text-sm tabular-nums {isOn ? 'text-signal' : 'text-fg-subtle'}">{row.skip ? '—' : row.n}</span>
						<span class="alpha-checker h-8 w-8 flex-none overflow-hidden rounded {row.skip ? 'opacity-40 grayscale' : ''}" data-slice-name-thumb>{#if row.thumb}<img src={row.thumb} alt="" class="h-full w-full object-cover" />{/if}</span>
						<Input
							value={row.skip ? '' : row.name}
							placeholder={row.skip ? 'Skipped' : ''}
							class="h-7 min-w-0 flex-1 px-2 font-mono text-base"
							aria-label="Name of cell {row.n}"
							disabled={row.skip || locked}
							onchange={(event: Event) => onrename(row.id, (event.currentTarget as HTMLInputElement).value)}
						/>
						<button
							type="button"
							class="grid h-7 w-7 flex-none place-items-center rounded text-fg-muted hover:bg-surface-2 hover:text-fg disabled:opacity-50"
							aria-label={row.skip ? 'Keep cell' : `Skip ${row.name}`}
							aria-pressed={row.skip}
							disabled={locked}
							use:tooltip={row.skip ? 'Keep' : 'Skip'}
							onclick={() => onskip(row.id)}
							data-slice-name-skip
						>
							<Icon name={row.skip ? 'editor-eye-off' : 'editor-eye'} className="h-3.5 w-3.5" />
						</button>
					</li>
				{/each}
			</ol>
		</section>

		<div class="h-px bg-line"></div>

		<section class="flex flex-col gap-2" data-slice-section="output">
			<EntryForm fields={saveFields} {values} label="Save to" onValues={writeSave} />
			<p class="font-mono text-sm tabular-nums text-fg-subtle" data-slice-count>{keptCount} {keptCount === 1 ? 'image' : 'images'} · PNG · Library</p>
		</section>
	{/if}
{/if}

<style>
	.swatch-checker {
		background: repeating-conic-gradient(rgb(var(--surface-3)) 0 25%, rgb(var(--surface-1)) 0 50%) 0 0 / 6px 6px;
	}
</style>
