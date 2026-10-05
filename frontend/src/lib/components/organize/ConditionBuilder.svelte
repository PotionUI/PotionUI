<script lang="ts">
	import CustomSelect from '$lib/components/CustomSelect.svelte';
	import { factIcon } from '$lib/organize/icons';
	import { Button, IconButton, SegmentedControl } from '$lib/components/ui';
	import {
		coerceValue,
		defaultValue,
		effectiveKind,
		factsForSubject,
		newCondition,
		operatorLabel,
		type RuleDraft
	} from '$lib/organize/draft';
	import type { OrganizeCatalog } from '$lib/types/organize';
	import ConditionValueControl from './ConditionValueControl.svelte';

	let {
		draft = $bindable(),
		catalog
	}: {
		draft: RuleDraft;
		catalog: OrganizeCatalog;
	} = $props();

	const facts = $derived(factsForSubject(catalog, draft.subject));

	function specOf(key: string) {
		return catalog.facts.find((f) => f.key === key);
	}

	function changeFact(index: number, key: string) {
		const spec = specOf(key);
		if (!spec) return;
		const fresh = newCondition(spec);
		draft.conditions[index] = { ...fresh, uid: draft.conditions[index].uid };
	}

	function changeOperator(index: number, operator: string) {
		const cond = draft.conditions[index];
		const spec = specOf(cond.fact);
		const kind = effectiveKind(spec);
		draft.conditions[index] = {
			...cond,
			operator,
			value: coerceValue(kind, operator, cond.value ?? defaultValue(kind, operator, spec), spec)
		};
	}

	function changeValue(index: number, value: unknown) {
		draft.conditions[index] = { ...draft.conditions[index], value };
	}

	function addCondition() {
		const first = facts[0];
		if (first) draft.conditions.push(newCondition(first));
	}

	function removeCondition(index: number) {
		draft.conditions.splice(index, 1);
	}
</script>

<div class="space-y-3">
	{#if draft.conditions.length === 0}
		<p class="text-sm text-fg-muted" data-testid="no-conditions">
			No conditions: the rule applies to every new {draft.subject === 'generation' ? 'generation' : draft.subject === 'upload' ? 'upload' : 'model'}.
		</p>
	{:else}
		<div class="flex items-center gap-2">
			<SegmentedControl
				variant="toggle"
				ariaLabel="Match"
				selected={draft.match}
				items={[
					{ id: 'all', label: 'All' },
					{ id: 'any', label: 'Any' }
				]}
				onSelect={(id) => (draft.match = id as 'all' | 'any')}
			/>
			<span class="text-xs text-fg-muted">of these are true</span>
		</div>
	{/if}

	{#each draft.conditions as cond, index (cond.uid)}
		{@const spec = specOf(cond.fact)}
		<div class="flex flex-wrap items-start gap-2 sm:flex-nowrap" data-testid="condition-row">
			<span class="w-10 flex-shrink-0 pt-2 font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">
				{index === 0 ? 'If' : draft.match === 'all' ? 'And' : 'Or'}
			</span>
			<div class="w-44 flex-shrink-0">
				<CustomSelect
					value={cond.fact}
					options={facts.map((f) => ({ value: f.key, label: f.label, icon: factIcon(f.key) }))}
					on:change={(event) => changeFact(index, event.detail)}
				/>
			</div>
			{#if spec}
				<div class="w-40 flex-shrink-0">
					<CustomSelect
						value={cond.operator}
						options={spec.operators.map((op) => ({ value: op, label: operatorLabel(catalog, op) }))}
						on:change={(event) => changeOperator(index, event.detail)}
					/>
				</div>
				<ConditionValueControl
					{spec}
					operator={cond.operator}
					value={cond.value}
					subject={draft.subject}
					onchange={(next) => changeValue(index, next)}
				/>
			{:else}
				<p class="flex-1 pt-2 text-sm text-warning">This condition is no longer available.</p>
			{/if}
			<IconButton icon="close" label="Remove condition" onclick={() => removeCondition(index)} />
		</div>
	{/each}

	<div class="flex flex-wrap items-center gap-3">
		<Button size="sm" variant="ghost" icon="plus" onclick={addCondition} disabled={facts.length === 0}>
			Add condition
		</Button>
		{#if draft.conditions.length > 1}
			<span class="text-xs text-fg-subtle">Need "A and (B or C)"? Make two rules and each one stays easy to read.</span>
		{/if}
	</div>
</div>
