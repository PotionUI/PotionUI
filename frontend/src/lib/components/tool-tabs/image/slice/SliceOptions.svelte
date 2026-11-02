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
	import CustomSelect from '$lib/components/CustomSelect.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import NumberStepper from '$lib/components/NumberStepper.svelte';
	import { Button, Input, Kbd, SegmentedControl, Switch } from '$lib/components/ui';
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

	let patternInput = $state<HTMLInputElement | null>(null);
	let cellCount = $derived(params.cells.length);
	let keptCount = $derived(params.cells.filter((cell) => !cell.skip).length);
	let current = $derived(cellCount ? tab : 'layout');
	let selectedSkip = $derived(selected.length === 1 ? !!params.cells.find((cell) => cell.id === selected[0])?.skip : false);

	const chip =
		'rounded border font-mono text-sm tabular-nums transition-colors focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-1 focus-visible:outline-signal disabled:cursor-not-allowed disabled:opacity-50';
	const on = 'border-signal/60 bg-signal/10 text-signal';
	const off = 'border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg';
	const head = 'font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle';

	function insertToken(token: string) {
		const el = patternInput;
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
			<div class="grid grid-cols-2 gap-2">
				<div class="flex flex-col gap-1">
					<span class="text-sm text-fg-muted">Columns</span>
					<NumberStepper value={params.layout.cols} min={1} max={MAX_GRID} label="Columns" onChange={(value) => onpreset(value, params.layout.rows)} />
				</div>
				<div class="flex flex-col gap-1">
					<span class="text-sm text-fg-muted">Rows</span>
					<NumberStepper value={params.layout.rows} min={1} max={MAX_GRID} label="Rows" onChange={(value) => onpreset(params.layout.cols, value)} />
				</div>
			</div>
			<div class="flex items-center gap-2 pt-0.5">
				<Switch checked={params.layout.snap} size="sm" label="Snap to gutters" onchange={(value) => onsnap(value)} />
				<span class="text-base text-fg">Snap to gutters</span>
				<span class="flex-1"></span>
				<span class="inline-flex items-center gap-1 text-sm text-fg-subtle"><Kbd keys={['Alt']} />frees</span>
			</div>
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
			<div class="flex items-center gap-2">
				<Switch checked={params.output.trim} size="sm" label="Trim background" disabled={locked} onchange={(value) => onoutput({ trim: value })} />
				<span class="text-base text-fg">Trim background</span>
			</div>
			<div class="grid grid-cols-2 gap-2">
				<div class="flex flex-col gap-1">
					<span class="text-sm text-fg-muted">Tolerance</span>
					<NumberStepper value={params.output.tolerance} min={0} max={64} label="Tolerance" onChange={(value) => onoutput({ tolerance: value })} />
				</div>
				<div class="flex flex-col gap-1">
					<span class="text-sm text-fg-muted">Padding, px</span>
					<NumberStepper value={params.output.padding} min={0} max={256} step={4} label="Padding" onChange={(value) => onoutput({ padding: value })} />
				</div>
			</div>
			<SegmentedControl
				variant="toggle"
				fullWidth
				ariaLabel="Aspect for every cell"
				items={[
					{ id: 'keep', label: 'Keep' },
					{ id: 'square', label: 'Square' },
					{ id: 'portrait', label: 'Portrait 2:3' }
				]}
				selected={params.output.aspect}
				onSelect={(id) => onoutput({ aspect: id as SliceAspect })}
			/>
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

		<section class="flex flex-col gap-2" data-slice-section="names">
			<p class={head}>Names</p>
			<Input
				value={params.pattern}
				class="h-8 px-2.5 font-mono text-base"
				aria-label="Name pattern"
				disabled={locked}
				onfocus={(event: FocusEvent) => (patternInput = event.currentTarget as HTMLInputElement)}
				oninput={(event: Event) => onpattern((event.currentTarget as HTMLInputElement).value)}
				data-slice-pattern
			/>
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
						<span class="h-8 w-8 flex-none rounded bg-surface-2 bg-cover bg-center {row.skip ? 'opacity-40 grayscale' : ''}" style={row.thumb ? `background-image:url(${row.thumb})` : ''}></span>
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
			<p class={head}>Save to</p>
			<CustomSelect
				value={params.collection ?? ''}
				options={[{ value: '', label: 'No collection' }, ...collections]}
				fill
				triggerIcon={false}
				disabled={locked}
				on:change={(event) => oncollection(event.detail ? String(event.detail) : null)}
			/>
			<p class="font-mono text-sm tabular-nums text-fg-subtle" data-slice-count>{keptCount} {keptCount === 1 ? 'image' : 'images'} · PNG · Library</p>
		</section>
	{/if}
{/if}

<style>
	.swatch-checker {
		background: repeating-conic-gradient(rgb(var(--surface-3)) 0 25%, rgb(var(--surface-1)) 0 50%) 0 0 / 6px 6px;
	}
</style>
