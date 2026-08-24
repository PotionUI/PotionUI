<!--
	A gate is a card that owns a boolean AND the fields that boolean governs -
	unlike section/group/accordion, it keeps its `name` and carries a real
	value. See src/features/fields/gate.py.

	Rendered in FieldChildren.svelte's `structuralTypes`, so it receives
	`name={null}` and the FULL ambient `value` object (its own key lives at
	`value[config.name]`, same object its children read/write through
	FieldChildren). See gateState.ts for the pure lookup helpers.
-->
<script lang="ts">
	import Icon from '../Icon.svelte';
	import Tooltip from '../Tooltip.svelte';
	import FieldChildren from './FieldChildren.svelte';
	import { gateHasChildren, gateRegionId, resolveGateOn } from './gateState';

	export let config: any = {};
	export let value: any = undefined;
	export let onChange: (fieldName: string, value: any) => void = () => {};
	export let onOriginChange: ((fieldName: string, origin: unknown) => void) | undefined = undefined;
	export let onMaskChange: ((fieldName: string, maskPath: string | undefined) => void) | undefined = undefined;
	export let fieldPath: string | undefined = undefined;

	$: fieldName = config.name || 'gate';
	$: label = config.title || config.label || '';
	$: summary = config.summary || '';
	$: experimental = config.experimental === true;
	$: hasChildren = gateHasChildren(config);
	$: regionId = gateRegionId(config);
	$: isOn = resolveGateOn(config, value);

	function handleChange(event: Event) {
		const target = event.target as HTMLInputElement;
		onChange(fieldName, target.checked);
	}
</script>

<div class="field-card">
	<div class="rounded-lg border border-line-strong bg-surface-1 p-3">
		<label
			for={fieldName}
			class="flex w-full cursor-pointer select-none items-center justify-between gap-3"
		>
			<span class="min-w-0">
				<span class="inline-flex items-center gap-1.5 text-sm font-medium text-fg">
					{label}
					{#if experimental}
						<Tooltip
							text="This feature is experimental — behavior and results may change."
							position="top"
						>
							<span
								class="inline-flex items-center rounded border border-warning/25 bg-warning/10 px-1.5 py-0.5 text-2xs font-medium text-warning"
							>
								Experimental
							</span>
						</Tooltip>
					{/if}
				</span>
				{#if !isOn && summary}
					<span class="block text-xs text-fg-muted mt-0.5">{summary}</span>
				{/if}
			</span>
			<span class="relative inline-flex h-5 w-9 shrink-0 items-center">
				<input
					type="checkbox"
					id={fieldName}
					checked={isOn}
					on:change={handleChange}
					class="peer sr-only"
					aria-expanded={hasChildren ? isOn : undefined}
					aria-controls={hasChildren ? regionId : undefined}
				/>
				<span
					class="pointer-events-none absolute inset-0 rounded-full border border-line-strong bg-surface-3 transition-colors duration-150 ease-out peer-checked:border-signal-solid peer-checked:bg-signal-solid peer-focus-visible:ring-2 peer-focus-visible:ring-signal peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-canvas"
				></span>
				<span
					class="pointer-events-none absolute left-0.5 h-4 w-4 rounded-full bg-white transition-transform duration-150 ease-out peer-checked:translate-x-4"
				></span>
			</span>
		</label>

		{#if hasChildren}
			<!-- Children stay mounted while off (max-height/opacity, not {#if}) so
			     their values survive toggling and still submit. -->
			<div
				id={regionId}
				class="{isOn
					? 'overflow-visible'
					: 'overflow-hidden'} transition-[max-height,opacity] duration-300 ease-out motion-reduce:duration-0"
				style="max-height: {isOn ? '4000px' : '0px'}; opacity: {isOn ? 1 : 0};"
			>
				<div class="space-y-2 pt-3">
					<FieldChildren children={config.children} {value} {onChange} {onOriginChange} {onMaskChange} location="gate" {fieldPath} />
				</div>
			</div>
		{/if}
	</div>
</div>
