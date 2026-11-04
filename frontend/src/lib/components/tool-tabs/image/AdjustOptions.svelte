<script lang="ts">
	import CurvePlot from '$lib/components/imageEditor/CurvePlot.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { IconButton, SegmentedControl, Switch } from '$lib/components/ui';
	import PropertySlider from '$lib/components/tool-tabs/frame/PropertySlider.svelte';
	import type { OpSpec } from '$lib/filters/engine';
	import { scalarParams } from '$lib/filters/steps';
	import type { EditStackState } from '$lib/tool-tabs/frame/editStack';
	import {
		ADJUST_SECTIONS,
		CAPTIONED_OPS,
		adjustValues,
		controlOps,
		opDefaults,
		sectionEdited,
		toggleIsOn,
		type AdjustControl,
		type AdjustSection
	} from '$lib/tool-tabs/image/adjustSections';
	import { CURVE_POINTS, curveMaster } from '$lib/tool-tabs/image/steps';

	let {
		stack,
		lookup,
		phone = false,
		onset,
		oncommit,
		ontoggle,
		onreset
	}: {
		stack: EditStackState<unknown>;
		lookup: (op: string) => OpSpec | undefined;
		phone?: boolean;
		onset: (op: string, param: string, value: number) => void;
		oncommit: () => void;
		ontoggle: (op: string, on: boolean) => void;
		onreset: (ops: string[]) => void;
	} = $props();

	let closed = $state<Record<string, boolean>>({});
	let tab = $state<AdjustSection['id']>('light');
	let shown = $derived(phone ? ADJUST_SECTIONS.filter((section) => section.id === tab) : ADJUST_SECTIONS);

	function spec(op: string): OpSpec | undefined {
		return lookup(op);
	}

	function sliderParam(control: Extract<AdjustControl, { kind: 'slider' }>) {
		return scalarParams(spec(control.op)).find((param) => param.id === control.param);
	}

	function valuesOf(op: string): Record<string, number> {
		return adjustValues(stack, op, opDefaults(op, spec(op)));
	}

	function captionFor(control: AdjustControl, index: number, list: AdjustControl[]): string | null {
		if (!CAPTIONED_OPS.includes(control.op)) return null;
		if (index > 0 && list[index - 1].op === control.op) return null;
		return spec(control.op)?.label ?? null;
	}

	function step(type: string): number {
		return type === 'float' ? 0.05 : 1;
	}
</script>

{#snippet control(item: AdjustControl, index: number, list: AdjustControl[])}
	{@const caption = captionFor(item, index, list)}
	{#if caption}
		<p class="pt-1 text-sm font-semibold text-fg-muted" data-adjust-caption={item.op}>{caption}</p>
	{/if}
	{#if item.kind === 'slider'}
		{@const param = sliderParam(item)}
		{#if param}
			<PropertySlider
				label={item.label ?? param.label}
				value={valuesOf(item.op)[param.id] ?? param.default}
				min={param.min}
				max={param.max}
				step={step(param.type)}
				defaultValue={param.default}
				unit={param.unit ?? ''}
				onchange={(value) => onset(item.op, param.id, value)}
				{oncommit}
			/>
		{/if}
	{:else if item.kind === 'curves'}
		<CurvePlot curves={[{ id: 'master', points: curveMaster(valuesOf('curves')) }]} label="Curves" />
		{#each CURVE_POINTS as point (point.id)}
			<PropertySlider
				label={point.label}
				value={valuesOf('curves')[point.id] ?? 0}
				min={-100}
				max={100}
				defaultValue={0}
				onchange={(value) => onset('curves', point.id, value)}
				{oncommit}
			/>
		{/each}
	{:else if spec(item.op)}
		<div class="flex h-8 items-center justify-between gap-2">
			<span class="font-mono text-sm uppercase tracking-[0.08em] {toggleIsOn(stack, item.op) ? 'text-fg-muted' : 'text-fg-subtle'}">{spec(item.op)?.label}</span>
			<Switch size="sm" label={spec(item.op)?.label ?? item.op} checked={toggleIsOn(stack, item.op)} onchange={(on) => ontoggle(item.op, on)} />
		</div>
	{/if}
{/snippet}

{#if phone}
	<SegmentedControl
		variant="toggle"
		fullWidth
		ariaLabel="Adjustment section"
		items={ADJUST_SECTIONS.map((section) => ({ id: section.id, label: section.label }))}
		selected={tab}
		onSelect={(id) => (tab = id as AdjustSection['id'])}
	/>
{/if}

<div class="flex flex-col {phone ? 'max-h-[34vh] gap-3 overflow-y-auto pr-1' : 'gap-5'}" data-adjust-options>
	{#each shown as section (section.id)}
		{@const edited = sectionEdited(section, stack, lookup)}
		{@const open = phone || !closed[section.id]}
		<section class="flex flex-col gap-3" data-adjust-section={section.id} data-edited={edited || undefined}>
			{#if !phone}
				<div class="flex items-center gap-2">
					<button
						type="button"
						class="flex min-w-0 flex-1 items-center gap-2 text-left text-base font-semibold text-fg focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-2 focus-visible:outline-signal"
						aria-expanded={open}
						onclick={() => (closed = { ...closed, [section.id]: open })}
						data-adjust-toggle={section.id}
					>
						<Icon name={section.icon} className="h-3.5 w-3.5 flex-none text-fg-muted" />
						<span>{section.label}</span>
						{#if edited}<span class="h-1.5 w-1.5 flex-none rounded-full bg-signal" aria-label="Has adjustments" data-adjust-dot></span>{/if}
					</button>
					{#if edited}
						<IconButton icon="refresh" label="Reset {section.label}" size="xs" onclick={() => onreset(controlOps(section))} data-adjust-reset={section.id} />
					{/if}
					<Icon name={open ? 'chevron-up' : 'chevron-down'} className="h-3.5 w-3.5 flex-none text-fg-subtle" />
				</div>
			{/if}
			{#if open}
				<div class="flex flex-col gap-2.5">
					{#each section.controls as item, index (`${item.op}:${item.kind === 'slider' ? item.param : item.kind}`)}
						{@render control(item, index, section.controls)}
					{/each}
				</div>
			{/if}
		</section>
	{/each}
</div>
