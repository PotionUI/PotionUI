<script lang="ts">
	import CustomSelect from '$lib/components/CustomSelect.svelte';
	import { api } from '$lib/services/api';
	import {
		ATTRIBUTE_TYPES,
		attributesForDraft,
		attributeValueSpec,
		coerceValue,
		isAttributeValue,
		operatorLabel,
		operatorsFor,
		pickAttribute
	} from '$lib/organize/draft';
	import type {
		OrganizeAttributeOption,
		OrganizeCatalog,
		OrganizeCondition,
		OrganizeFactSpec,
		OrganizeOption,
		OrganizeSubject
	} from '$lib/types/organize';
	import ConditionValueControl from './ConditionValueControl.svelte';

	let {
		spec,
		catalog,
		operator,
		value,
		subject,
		conditions = [],
		onchange
	}: {
		spec: OrganizeFactSpec;
		catalog: OrganizeCatalog;
		operator: string;
		value: unknown;
		subject: OrganizeSubject;
		conditions?: Pick<OrganizeCondition, 'fact' | 'operator' | 'value'>[];
		onchange: (next: { operator: string; value: unknown }) => void;
	} = $props();

	let attributes = $state<OrganizeAttributeOption[]>([]);
	let loadedFor = $state('');

	function isAttributeOption(option: OrganizeOption): option is OrganizeAttributeOption {
		const type = (option.meta as { type?: unknown } | undefined)?.type;
		return typeof type === 'string' && (ATTRIBUTE_TYPES as readonly string[]).includes(type);
	}

	$effect(() => {
		const key = `${subject}:${spec.key}`;
		if (loadedFor === key) return;
		loadedFor = key;
		void api
			.getOrganizeFactOptions(spec.key, { subject, limit: 200 })
			.then((response) => {
				attributes = response.success && Array.isArray(response.data) ? response.data.filter(isAttributeOption) : [];
			})
			.catch(() => {
				attributes = [];
			});
	});

	const current = $derived(isAttributeValue(value) ? value : null);
	const picked = $derived(attributes.find((a) => a.value === current?.key));
	const visible = $derived(attributesForDraft(attributes, conditions, current?.key ?? ''));
	const attributeOptions = $derived.by(() => {
		const out = visible.map((a) => ({ value: a.value, label: a.label, description: a.meta.type_label ?? a.meta.type }));
		if (current?.key && !picked) out.unshift({ value: current.key, label: current.label || current.key, description: '' });
		return out;
	});
	const operators = $derived(operatorsFor(spec, { value }));
	const valueSpec = $derived(current?.type ? attributeValueSpec(spec, picked, current.type) : null);

	function chooseAttribute(key: string) {
		const option = attributes.find((a) => a.value === key);
		if (!option || option.value === current?.key) return;
		onchange(pickAttribute(spec, option));
	}

	function chooseOperator(next: string) {
		onchange({ operator: next, value: coerceValue('attribute', next, value, spec) });
	}

	function changeInner(next: unknown) {
		if (!current) return;
		onchange({ operator, value: { ...current, value: next } });
	}
</script>

<div class="flex min-w-0 flex-1 flex-wrap items-start gap-2 sm:flex-nowrap" data-testid="attribute-condition" data-kind="attribute" data-fact={spec.key}>
	<div class="w-48 flex-shrink-0" data-testid="attribute-picker">
		<CustomSelect
			value={current?.key ?? ''}
			searchable
			placeholder="Choose an attribute"
			options={attributeOptions}
			on:change={(event) => chooseAttribute(event.detail)}
		/>
	</div>
	{#if current?.key}
		<div class="w-40 flex-shrink-0" data-testid="attribute-operator">
			<CustomSelect
				value={operator}
				options={operators.map((op) => ({ value: op, label: operatorLabel(catalog, op) }))}
				on:change={(event) => chooseOperator(event.detail)}
			/>
		</div>
		{#if valueSpec}
			<ConditionValueControl spec={valueSpec} {operator} value={current.value} {subject} onchange={changeInner} />
		{/if}
	{/if}
</div>
