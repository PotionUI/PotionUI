<script lang="ts">
	import CurvePlot from '$lib/components/paint/CurvePlot.svelte';
	import { Switch } from '$lib/components/ui';
	import PropertySlider from '$lib/components/tool-tabs/frame/PropertySlider.svelte';
	import ToolForm from '$lib/media-tools/ToolForm.svelte';
	import { effectDefaults } from '$lib/effects/catalog';
	import { getOp } from '$lib/filters/engine';
	import { filterCatalog } from '$lib/filters/catalog';
	import { sharedExtensions } from '$lib/filters/extensions';
	import { scalarParams } from '$lib/filters/steps';
	import { mediaToolCatalog } from '$lib/media-tools/catalog';
	import { paintFilters } from '$lib/paint/registries';
	import { createStack, liveEdit, type EditStackState, type StepDraft } from '$lib/tool-tabs/frame/editStack';
	import { adjustStepFor, setAdjustToggle, setAdjustValue, toggleIsOn } from '$lib/tool-tabs/image/adjustSections';
	import {
		GRADE_ENTRIES,
		LIVE_EFFECTS,
		OP_ENTRIES,
		gradeValue,
		isGradeEntry,
		isLiveEffectEntry,
		isOpEntry,
		liveEffectState,
		matchEffect,
		setGradeValue,
		controlKey
	} from '$lib/tool-tabs/image/entries';
	import { imageEffects } from '$lib/tool-tabs/image/shaderEffect';
	import { CURVE_POINTS, curveMaster, toolEffectDraft, type AdjustParams, type ToolEffectParams } from '$lib/tool-tabs/image/steps';
	import type { ImageToolHost } from '$lib/tool-tabs/image/toolHost';

	let { host, tool }: { host: unknown; phone?: boolean; tool: string } = $props();

	let image = $derived(host as ImageToolHost);
	let stack = $state.raw<EditStackState<unknown>>(createStack());

	$effect(() => image.stack.subscribe((next) => (stack = next)));
	let extensions = $derived(sharedExtensions($filterCatalog.ops, paintFilters.list()));
	const lookup = (op: string) => getOp(op, extensions);

	function step(type: string): number {
		return type === 'float' ? 0.05 : 1;
	}

	let fx = $derived(imageEffects($mediaToolCatalog).find((candidate) => candidate.id === tool) ?? null);
	let effectState = $derived(isLiveEffectEntry(tool) ? liveEffectState(stack, LIVE_EFFECTS[tool]) : null);
	let effectValues = $derived<Record<string, unknown>>(
		effectState?.shape === 'effect' ? { ...(effectState.step!.params as ToolEffectParams).values } : fx ? effectDefaults(fx) : {}
	);

	function writeEffect(name: string, value: unknown) {
		if (!fx) return;
		const params: ToolEffectParams = { kind: 'effect', tool, name: fx.label, values: { ...effectValues, [name]: value } };
		image.edit(liveEdit(stack, toolEffectDraft(params, $mediaToolCatalog) as StepDraft<unknown>, matchEffect(tool)));
	}

	function legacyValue(op: string, param: string, fallback: number): number {
		const found = adjustStepFor(stack, op);
		return found && !found.hidden ? ((found.params as AdjustParams).values[param] ?? fallback) : fallback;
	}

	function writeOp(op: string, param: string, value: number) {
		const spec = lookup(op);
		if (spec) image.edit(setAdjustValue(stack, spec, param, value));
	}
</script>

<div class="flex flex-col gap-2.5" data-entry-options={tool}>
	{#if isGradeEntry(tool)}
		{@const entry = GRADE_ENTRIES[tool]}
		{#each entry.controls as control (controlKey(control))}
			{@const param = scalarParams(lookup(control.op)).find((candidate) => candidate.id === control.param)}
			{#if param}
				<PropertySlider
					label={control.label}
					value={gradeValue(stack, entry, control, lookup)}
					min={param.min}
					max={param.max}
					step={step(param.type)}
					defaultValue={param.default}
					unit={param.unit ?? ''}
					onchange={(value) => image.edit(setGradeValue(stack, entry, control, value, lookup))}
					oncommit={() => image.settle()}
				/>
			{/if}
		{/each}
	{:else if isOpEntry(tool)}
		{@const entry = OP_ENTRIES[tool]}
		{#if entry.kind === 'sliders'}
			{#each entry.controls ?? [] as control (control.param)}
				{@const param = scalarParams(lookup(entry.op)).find((candidate) => candidate.id === control.param)}
				{#if param}
					<PropertySlider
						label={control.label}
						value={legacyValue(entry.op, control.param, param.default)}
						min={param.min}
						max={param.max}
						step={step(param.type)}
						defaultValue={param.default}
						unit={param.unit ?? ''}
						onchange={(value) => writeOp(entry.op, control.param, value)}
						oncommit={() => image.settle()}
					/>
				{/if}
			{/each}
		{:else if entry.kind === 'curves'}
			{@const values = Object.fromEntries(CURVE_POINTS.map((point) => [point.id, legacyValue('curves', point.id, 0)]))}
			<CurvePlot curves={[{ id: 'master', points: curveMaster(values) }]} label="Curves" />
			{#each CURVE_POINTS as point (point.id)}
				<PropertySlider label={point.label} value={values[point.id]} min={-100} max={100} defaultValue={0} onchange={(value) => writeOp('curves', point.id, value)} oncommit={() => image.settle()} />
			{/each}
		{:else}
			{@const spec = lookup(entry.op)}
			<div class="flex h-8 items-center justify-between gap-2">
				<span class="text-base text-fg">{spec?.label ?? entry.op}</span>
				<Switch
					size="sm"
					label={spec?.label ?? entry.op}
					checked={toggleIsOn(stack, entry.op)}
					onchange={(on) => spec && image.edit(setAdjustToggle(stack, spec, on))}
				/>
			</div>
		{/if}
	{:else if isLiveEffectEntry(tool)}
		{@const legacy = LIVE_EFFECTS[tool]}
		{#if effectState?.shape === 'legacy'}
			{#each Object.entries(legacy.legacy) as [name, opParam] (name)}
				{@const param = scalarParams(lookup(legacy.legacyOp)).find((candidate) => candidate.id === opParam)}
				{@const label = fx?.params.find((candidate) => candidate.name === name)?.label ?? param?.label ?? name}
				{#if param}
					<PropertySlider
						{label}
						value={legacyValue(legacy.legacyOp, opParam, param.default)}
						min={param.min}
						max={param.max}
						step={step(param.type)}
						defaultValue={param.default}
						onchange={(value) => writeOp(legacy.legacyOp, opParam, value)}
						oncommit={() => image.settle()}
					/>
				{/if}
			{/each}
		{:else if fx}
			<ToolForm params={fx.params} values={effectValues} onChange={writeEffect} />
		{/if}
	{/if}
</div>
