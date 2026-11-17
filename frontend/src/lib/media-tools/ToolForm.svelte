<script lang="ts">
	import SegmentedField from '$lib/components/form-fields/SegmentedField.svelte';
	import SelectField from '$lib/components/form-fields/SelectField.svelte';
	import SliderField from '$lib/components/form-fields/SliderField.svelte';
	import SeedField from '$lib/components/form-fields/SeedField.svelte';
	import NumberBoxField from '$lib/components/form-fields/NumberBoxField.svelte';
	import CheckboxField from '$lib/components/form-fields/CheckboxField.svelte';
	import ColorField from '$lib/components/form-fields/ColorField.svelte';
	import AnchorField from '$lib/components/form-fields/AnchorField.svelte';
	import TextInput from '$lib/components/form-fields/TextInput.svelte';
	import { controlFor, toFieldConfig, visibleParams, type ControlKind } from './params';
	import type { ToolParam } from './types';

	let {
		params,
		values,
		errors = {},
		onChange
	}: {
		params: ToolParam[];
		values: Record<string, unknown>;
		errors?: Record<string, string>;
		onChange: (name: string, value: unknown) => void;
	} = $props();

	function invalidMarker(node: HTMLElement, message: string | undefined) {
		function mark(next: string | undefined) {
			for (const element of node.querySelectorAll('input, select, textarea')) {
				if (next) {
					element.setAttribute('aria-invalid', 'true');
					element.setAttribute('aria-describedby', `${node.dataset.param}-server-error`);
				} else {
					element.removeAttribute('aria-describedby');
					if (element.getAttribute('aria-invalid') === 'true') element.removeAttribute('aria-invalid');
				}
			}
		}
		mark(message);
		return { update: mark };
	}

	const COMPONENTS: Record<ControlKind, any> = {
		segmented: SegmentedField,
		dropdown: SelectField,
		slider: SliderField,
		number: NumberBoxField,
		seed: SeedField,
		switch: CheckboxField,
		color: ColorField,
		anchor: AnchorField,
		text: TextInput
	};

	type Row = { key: string; params: ToolParam[]; paired: boolean };

	function rowsOf(list: ToolParam[]): Row[] {
		const rows: Row[] = [];
		for (const param of list) {
			const last = rows[rows.length - 1];
			if (controlFor(param) === 'number' && last?.paired) {
				last.params.push(param);
				last.key += `|${param.name}`;
			} else {
				rows.push({ key: param.name, params: [param], paired: controlFor(param) === 'number' });
			}
		}
		return rows;
	}

	let rows = $derived(rowsOf(visibleParams(params, values)));
</script>

{#each rows as row (row.key)}
	<div class={row.params.length > 1 ? 'grid grid-cols-2 gap-3' : ''}>
		{#each row.params as param (param.name)}
			{@const control = controlFor(param)}
			{@const Control = COMPONENTS[control]}
			{@const error = errors[param.name]}
			<div
				data-param={param.name}
				data-control={control}
				data-invalid={error ? 'true' : undefined}
				use:invalidMarker={error}
				class="min-w-0 {error ? '-m-1.5 rounded p-1.5 ring-1 ring-danger/60' : ''}"
			>
				<Control
					name={param.name}
					config={toFieldConfig(param)}
					value={values[param.name]}
					onChange={(name: string, next: unknown) => onChange(name, next)}
				/>
				{#if error}
					<p
						id="{param.name}-server-error"
						role="alert"
						data-param-error
						class="mt-1 font-mono text-sm text-danger"
					>
						{error}
					</p>
				{/if}
			</div>
		{/each}
	</div>
{/each}
