<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import CurvePlot from '$lib/components/imageEditor/CurvePlot.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import ToolForm from '$lib/media-tools/ToolForm.svelte';
	import { effectDefaults, effectGroups, sameValues, type EffectTool } from '$lib/effects/catalog';
	import SliderField from '$lib/components/form-fields/SliderField.svelte';
	import { paintFilters } from '$lib/components/imageEditor/registries';
	import { Spinner } from '$lib/components/ui';
	import EffectCardActions from '$lib/components/tool-tabs/frame/EffectCardActions.svelte';
	import { filterCatalog, loadCube, loadFilterCatalog } from '$lib/filters/catalog';
	import { getOp } from '$lib/filters/engine';
	import { sharedExtensions } from '$lib/filters/extensions';
	import { isLocked } from '$lib/filters/strip';
	import { scalarParams } from '$lib/filters/steps';
	import { makeThumbSource, renderThumb, ThumbCache } from '$lib/filters/thumbs';
	import { stillShader } from '$lib/tool-tabs/image/shaderEffect';
	import type { FilterItem } from '$lib/filters/types';
	import { ADJUST_OPS, CURVE_POINTS, curveMaster, type AdjustParams, type EffectParams, type LookParams, type ToolEffectParams } from '$lib/tool-tabs/image/steps';

	let {
		params,
		base = null,
		phone = false,
		effects = [],
		updating = false,
		original = null,
		busy = false,
		onapply,
		onchange
	}: {
		params: EffectParams | null;
		base?: HTMLCanvasElement | null;
		phone?: boolean;
		effects?: EffectTool[];
		updating?: boolean;
		original?: EffectParams | null;
		busy?: boolean;
		onapply: () => void;
		onchange: (next: EffectParams) => void;
	} = $props();

	let tab = $state<'looks' | 'adjust' | 'effects'>(untrack(() => (params?.kind === 'adjust' ? 'adjust' : params?.kind === 'effect' ? 'effects' : 'looks')));
	let query = $state('');
	let thumbSource = $state.raw<ImageData | null>(null);
	let revision = 0;
	const thumbs = new ThumbCache();

	onMount(() => {
		void loadFilterCatalog();
	});

	onDestroy(() => stillShader.dispose());

	let extensions = $derived(sharedExtensions($filterCatalog.ops, paintFilters.list()));
	let looks = $derived(
		$filterCatalog.items
			.filter((item) => !isLocked(item, extensions))
			.filter((item) => !query.trim() || item.name.toLowerCase().includes(query.trim().toLowerCase()))
	);
	let adjustOps = $derived(ADJUST_OPS.map((id) => getOp(id, extensions)).filter((op) => op !== undefined));
	let look = $derived(params?.kind === 'look' ? params : null);
	let adjust = $derived(params?.kind === 'adjust' ? params : null);
	let chosen = $derived(params?.kind === 'effect' ? params : null);
	let groups = $derived(effectGroups(effects));
	let chosenEffect = $derived(chosen ? (effects.find((effect) => effect.id === chosen.tool) ?? null) : null);
	let adjustDef = $derived(adjust ? getOp(adjust.op, extensions) : undefined);

	$effect(() => {
		const canvas = base;
		revision += 1;
		thumbSource = canvas ? makeThumbSource(canvas, phone ? 112 : 144) : null;
		thumbs.prune(revision);
	});

	function drawThumb(node: HTMLCanvasElement, input: { item: FilterItem; source: ImageData | null }) {
		let token = 0;
		async function paint(next: { item: FilterItem; source: ImageData | null }) {
			const mine = ++token;
			const image = next.source;
			if (!image) return;
			const at = revision;
			try {
				let rendered = thumbs.get(at, next.item);
				if (!rendered) {
					const cube = next.item.has_lut ? await loadCube(next.item) : null;
					rendered = renderThumb(image, next.item, cube, extensions);
					thumbs.set(at, next.item, rendered);
				}
				if (mine !== token) return;
				node.width = rendered.width;
				node.height = rendered.height;
				node.getContext('2d', { willReadFrequently: true })?.putImageData(rendered, 0, 0);
			} catch {
				return;
			}
		}
		void paint(input);
		return {
			update(next: { item: FilterItem; source: ImageData | null }) {
				void paint(next);
			},
			destroy() {
				token += 1;
			}
		};
	}

	function pickLook(item: FilterItem) {
		const intensity = look?.filter === item.id ? look.intensity : item.intensity;
		onchange({ kind: 'look', filter: item.id, name: item.name, intensity } satisfies LookParams);
	}

	function adjustDefaults(id: string): Record<string, number> {
		const def = getOp(id, extensions);
		const values: Record<string, number> = {};
		if (!def) return values;
		if (id === 'curves') for (const point of CURVE_POINTS) values[point.id] = 0;
		for (const param of scalarParams(def)) values[param.id] = param.default;
		if (id === 'grain' && !values.amount) values.amount = 30;
		return values;
	}

	function pickAdjust(id: string) {
		const def = getOp(id, extensions);
		if (!def) return;
		onchange({ kind: 'adjust', op: id, name: def.label, values: adjustDefaults(id) } satisfies AdjustParams);
	}

	function comparable(value: EffectParams): Record<string, unknown> {
		if (value.kind === 'look') return { filter: value.filter, intensity: value.intensity };
		if (value.kind === 'adjust') return { op: value.op, ...value.values };
		return { tool: value.tool, ...value.values };
	}

	function defaultsOf(value: EffectParams): EffectParams {
		if (value.kind === 'look') {
			const item = $filterCatalog.items.find((entry) => entry.id === value.filter);
			return { ...value, intensity: item?.intensity ?? value.intensity };
		}
		if (value.kind === 'adjust') return { ...value, values: adjustDefaults(value.op) };
		const effect = effects.find((entry) => entry.id === value.tool);
		return { ...value, values: effect ? effectDefaults(effect) : value.values } as ToolEffectParams;
	}

	function canReset(value: EffectParams): boolean {
		return !busy && !sameValues(comparable(value), comparable(defaultsOf(value)));
	}

	function canApply(value: EffectParams): boolean {
		if (busy) return false;
		return !updating || !original || !sameValues(comparable(value), comparable(original));
	}

	function pickEffect(effect: EffectTool) {
		if (chosen?.tool === effect.id) return;
		onchange({ kind: 'effect', tool: effect.id, name: effect.label, values: effectDefaults(effect) } satisfies ToolEffectParams);
	}

	function resetValues(value: EffectParams) {
		onchange(defaultsOf(value));
	}

	function setEffectValue(name: string, value: unknown) {
		if (!chosen) return;
		onchange({ ...chosen, values: { ...chosen.values, [name]: value } });
	}

	function setValue(key: string, value: number) {
		if (!adjust) return;
		onchange({ ...adjust, values: { ...adjust.values, [key]: value } });
	}
</script>

{#snippet effectBody(value: ToolEffectParams, effect: EffectTool)}
	{#if effect.description}
		<p class="text-sm text-fg-muted" data-effect-description>{effect.description}</p>
	{/if}
	{#if effect.params.length}
		<ToolForm params={effect.params} values={value.values} onChange={setEffectValue} />
	{/if}
	{@render actions(value)}
{/snippet}

{#snippet actions(value: EffectParams)}
	<EffectCardActions name={value.name} {updating} canApply={canApply(value)} canReset={canReset(value)} onapply={onapply} onreset={() => resetValues(value)} />
{/snippet}

<div class="flex items-center gap-4 border-b border-line" role="tablist" aria-label="Effects">
	<button
		type="button"
		role="tab"
		aria-selected={tab === 'looks'}
		class="-mb-px border-b-2 px-1 pb-2 text-base {tab === 'looks' ? 'border-fg text-fg' : 'border-transparent text-fg-muted hover:text-fg'}"
		onclick={() => (tab = 'looks')}
		data-effects-tab="looks">Looks <span class="font-mono text-sm tabular-nums text-fg-subtle">{$filterCatalog.items.length || ''}</span></button
	>
	<button
		type="button"
		role="tab"
		aria-selected={tab === 'adjust'}
		class="-mb-px border-b-2 px-1 pb-2 text-base {tab === 'adjust' ? 'border-fg text-fg' : 'border-transparent text-fg-muted hover:text-fg'}"
		onclick={() => (tab = 'adjust')}
		data-effects-tab="adjust">Adjust</button
	>
	{#if effects.length}
		<button
			type="button"
			role="tab"
			aria-selected={tab === 'effects'}
			class="-mb-px border-b-2 px-1 pb-2 text-base {tab === 'effects' ? 'border-fg text-fg' : 'border-transparent text-fg-muted hover:text-fg'}"
			onclick={() => (tab = 'effects')}
			data-effects-tab="effects">Effects</button
		>
	{/if}
</div>

{#if tab === 'looks'}
	{#if !phone}
		<input
			type="search"
			class="h-8 w-full rounded border border-line-strong bg-surface-2 px-2.5 text-base text-fg placeholder:text-fg-subtle focus:border-signal/60 focus:outline-none"
			placeholder="Search looks"
			aria-label="Search looks"
			bind:value={query}
		/>
	{/if}
	{#if $filterCatalog.status === 'loading' && looks.length === 0}
		<div class="flex justify-center py-6"><Spinner /></div>
	{:else if $filterCatalog.status === 'error'}
		<p class="text-sm text-danger">{$filterCatalog.error}</p>
	{:else}
		<div class={phone ? 'flex gap-2 overflow-x-auto pb-1' : 'grid grid-cols-2 gap-2'} role="listbox" aria-label="Looks" data-looks>
			{#each looks as item (item.id)}
				{@const selected = look?.filter === item.id}
				<button
					type="button"
					role="option"
					aria-selected={selected}
					class="flex flex-col gap-1 rounded-lg border p-1 text-left {phone ? 'w-[88px] flex-none' : ''} {selected
						? 'border-signal/70 bg-signal/10'
						: 'border-transparent hover:bg-surface-2'}"
					onclick={() => pickLook(item)}
					data-look={item.id}
				>
					<canvas use:drawThumb={{ item, source: thumbSource }} class="alpha-checker aspect-[4/3] w-full rounded object-cover" aria-hidden="true"></canvas>
					<span class="flex items-center gap-1 px-0.5 text-sm">
						<span class="min-w-0 flex-1 truncate {selected ? 'text-signal' : 'text-fg'}">{item.name}</span>
						{#if !phone && item.tags?.length}<span class="font-mono text-sm text-fg-subtle">{item.tags[item.tags.length - 1]}</span>{/if}
					</span>
				</button>
			{/each}
		</div>
	{/if}
	{#if look}
		<SliderField
			name="intensity"
			config={{ title: 'Intensity', min: 0, max: 100, step: 1, default: 100 }}
			value={look.intensity}
			onChange={(_name, value) => onchange({ ...look, intensity: Number(value) })}
		/>
		{@render actions(look)}
	{/if}
{:else if tab === 'effects'}
	{#if phone}
		<div class="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-0.5" role="listbox" aria-label="Effects" data-effect-strip>
			{#each effects as effect (effect.id)}
				<button
					type="button"
					role="option"
					aria-selected={chosen?.tool === effect.id}
					class="h-8 flex-none rounded border px-2.5 text-sm {chosen?.tool === effect.id
						? 'border-signal/60 bg-signal/10 text-signal'
						: 'border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg'}"
					onclick={() => pickEffect(effect)}
					data-effect-chip={effect.id}>{effect.label}</button
				>
			{/each}
		</div>
	{:else}
		{#each groups as group (group.id)}
			<div class="flex flex-col gap-1.5" data-effect-group={group.id}>
				<span class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">{group.label}</span>
				{#each group.effects as effect (effect.id)}
					{@const open = chosen?.tool === effect.id}
					<div class="rounded-lg border {open ? 'border-signal/50 bg-surface-2' : 'border-line bg-surface-1 hover:border-line-hover'}" data-effect-card={effect.id} data-open={open || undefined}>
						<button type="button" class="flex h-10 w-full items-center gap-2 px-3 text-left text-base text-fg" aria-expanded={open} onclick={() => pickEffect(effect)}>
							<Icon name={effect.icon} className="h-3.5 w-3.5 flex-none text-fg-muted" />
							<span class="min-w-0 flex-1 truncate">{effect.label}</span>
						</button>
						{#if open && chosen}
							<div class="flex flex-col gap-3 px-3 pb-3">
								{@render effectBody(chosen, effect)}
							</div>
						{/if}
					</div>
				{/each}
			</div>
		{/each}
	{/if}
	{#if phone}
		{#if chosen && chosenEffect}
			{@render effectBody(chosen, chosenEffect)}
		{:else}
			<p class="text-sm text-fg-muted">Pick an effect. It previews here and renders when you save.</p>
		{/if}
	{:else if !chosen}
		<p class="text-sm text-fg-muted">Pick an effect. It previews here and renders when you save.</p>
	{/if}
{:else}
	<div class="flex flex-wrap gap-1.5" role="listbox" aria-label="Adjustments" data-adjust-ops>
		{#each adjustOps as op (op.id)}
			<button
				type="button"
				role="option"
				aria-selected={adjust?.op === op.id}
				class="h-8 rounded border px-2.5 text-sm {adjust?.op === op.id
					? 'border-signal/60 bg-signal/10 text-signal'
					: 'border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg'}"
				onclick={() => pickAdjust(op.id)}
				data-adjust-op={op.id}>{op.label}</button
			>
		{/each}
	</div>
	{#if adjust}
		{#if adjust.op === 'curves'}
			<CurvePlot curves={[{ id: 'master', points: curveMaster(adjust.values) }]} label="Curves" />
			{#each CURVE_POINTS as point (point.id)}
				<SliderField
					name={point.id}
					config={{ title: point.label, min: -100, max: 100, step: 1, default: 0 }}
					value={adjust.values[point.id] ?? 0}
					onChange={(_name, value) => setValue(point.id, Number(value))}
				/>
			{/each}
		{:else}
			{#each scalarParams(adjustDef) as param (param.id)}
				<SliderField
					name={param.id}
					config={{ title: param.label, min: param.min, max: param.max, step: param.type === 'float' ? 0.05 : 1, default: param.default, unit: param.unit ?? '' }}
					value={adjust.values[param.id] ?? param.default}
					onChange={(_name, value) => setValue(param.id, Number(value))}
				/>
			{/each}
			{#if scalarParams(adjustDef).length === 0}
				<p class="text-sm text-fg-muted">{adjust.name} has no settings.</p>
			{/if}
		{/if}
		{@render actions(adjust)}
	{:else}
		<p class="text-sm text-fg-muted">Pick an adjustment. Each one applies as its own step.</p>
	{/if}
{/if}
