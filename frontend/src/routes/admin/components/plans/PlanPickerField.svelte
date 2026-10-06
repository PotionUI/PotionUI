<script lang="ts">
	import { Button } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import { EntityPicker } from '$lib/components/picker';
	import { createPlansKind, planLimitsSummary } from '$lib/components/picker/kinds/plans';
	import type { LimitKindDescriptor, Plan } from '$lib/plans/types';

	let {
		plans,
		kinds,
		value,
		inheritLabel,
		title = 'Choose a plan',
		subtitle = '',
		onChange
	}: {
		plans: readonly Plan[];
		kinds: readonly LimitKindDescriptor[];
		value: string | null;
		inheritLabel: string;
		title?: string;
		subtitle?: string;
		onChange: (planId: string | null) => void;
	} = $props();

	let open = $state(false);

	const plansKind = $derived(createPlansKind(kinds));
	const current = $derived(value ? (plans.find((plan) => plan.id === value) ?? null) : null);
	const assignedIds = $derived(new Set<string>(value ? [value] : []));
	const label = $derived(current ? current.name : value ? 'Unknown plan' : inheritLabel);
	const summary = $derived(current ? planLimitsSummary(current, kinds) : '');
</script>

<div class="flex flex-wrap items-center gap-2">
	<button
		type="button"
		class="flex min-w-0 max-w-full items-center gap-2 rounded border border-line bg-surface-2 px-3 py-1.5 text-left text-sm text-fg hover:border-line-hover"
		aria-haspopup="dialog"
		aria-expanded={open}
		data-plan-picker-trigger
		onclick={() => (open = true)}
	>
		<Icon name="layers" className="h-4 w-4 flex-shrink-0 text-fg-subtle" />
		<span class="min-w-0">
			<span class="block truncate font-medium" data-plan-picker-label>{label}</span>
			{#if summary}<span class="block truncate font-mono text-2xs text-fg-subtle" data-plan-picker-summary>{summary}</span>{/if}
		</span>
		<Icon name="chevron-down" className="h-3.5 w-3.5 flex-shrink-0 text-fg-subtle" />
	</button>
	{#if value !== null}
		<Button size="sm" variant="ghost" onclick={() => onChange(null)}>
			<span data-plan-inherit>Use inherited plan</span>
		</Button>
	{/if}
</div>

<EntityPicker
	kind={plansKind}
	items={plans}
	{assignedIds}
	isOpen={open}
	{title}
	{subtitle}
	mode="single"
	allowUnassign={false}
	onClose={() => (open = false)}
	onApply={(diff) => {
		const id = diff.add[0];
		if (id) onChange(id);
	}}
/>
