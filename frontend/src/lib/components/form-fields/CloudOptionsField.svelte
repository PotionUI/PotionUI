<script lang="ts">
	import { getContext } from 'svelte';
	import { writable, type Writable } from 'svelte/store';
	import { FORM_FIELD_ERRORS_CONTEXT_KEY } from '$lib/form/fieldErrorsContext';
	import { humanizeParamName, splitOptionErrors, type CapabilityParam } from '$lib/form/capabilityBinder';
	import SelectField from './SelectField.svelte';
	import SliderField from './SliderField.svelte';
	import NumberInput from './NumberInput.svelte';
	import CheckboxField from './CheckboxField.svelte';
	import TextInput from './TextInput.svelte';

	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;

	const serverErrors =
		getContext<Writable<Record<string, string[]>>>(FORM_FIELD_ERRORS_CONTEXT_KEY) ??
		writable<Record<string, string[]>>({});

	$: params = (config.resolved_params ?? []) as CapabilityParam[];
	$: current = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>;
	$: messages = name ? ($serverErrors[name] ?? []) : [];
	$: split = splitOptionErrors(
		messages,
		params.map((param) => param.name)
	);

	function fieldConfig(param: CapabilityParam): Record<string, unknown> {
		const base: Record<string, unknown> = {
			title: param.label || humanizeParamName(param.name),
			tooltip: param.description || undefined,
			default: param.default ?? undefined
		};
		if (param.kind === 'enum') {
			base.options = (param.values ?? []).map((entry) => ({ label: String(entry), value: entry }));
		} else if (param.kind === 'range') {
			base.minimum = param.minimum ?? undefined;
			base.maximum = param.maximum ?? undefined;
			base.step = param.step ?? (param.integer ? 1 : undefined);
		}
		return base;
	}

	function useSlider(param: CapabilityParam): boolean {
		return typeof param.minimum === 'number' && typeof param.maximum === 'number';
	}

	function handleParamChange(key: string, next: unknown) {
		if (!name) return;
		const merged = { ...current };
		if (next === null || next === undefined || next === '') delete merged[key];
		else merged[key] = next;
		onChange(name, merged);
	}
</script>

{#if params.length > 0}
	<div class="space-y-4" data-testid="cloud-options">
		{#each params as param (param.name)}
			<div data-option-name={param.name}>
				{#if param.kind === 'enum'}
					<SelectField name={param.name} config={fieldConfig(param)} value={current[param.name]} onChange={handleParamChange} />
				{:else if param.kind === 'range' && useSlider(param)}
					<SliderField name={param.name} config={fieldConfig(param)} value={current[param.name] ?? param.default} onChange={handleParamChange} />
				{:else if param.kind === 'range'}
					<NumberInput name={param.name} config={fieldConfig(param)} value={current[param.name]} onChange={handleParamChange} />
				{:else if param.kind === 'boolean'}
					<CheckboxField name={param.name} config={fieldConfig(param)} value={current[param.name]} onChange={handleParamChange} />
				{:else}
					<TextInput name={param.name} config={fieldConfig(param)} value={current[param.name] ?? ''} onChange={handleParamChange} />
				{/if}
				{#if split.byKey[param.name]?.length}
					<div class="mt-1 space-y-0.5" role="alert">
						{#each split.byKey[param.name] as message}
							<p class="text-xs text-danger">{message}</p>
						{/each}
					</div>
				{/if}
			</div>
		{/each}
		{#if split.rest.length > 0}
			<div class="space-y-0.5" role="alert">
				{#each split.rest as message}
					<p class="text-xs text-danger">{message}</p>
				{/each}
			</div>
		{/if}
	</div>
{/if}
