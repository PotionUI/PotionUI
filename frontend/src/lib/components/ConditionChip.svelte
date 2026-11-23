<script lang="ts">
	import { untrack } from 'svelte';
	import type { Readable } from 'svelte/store';
	import Icon from './Icon.svelte';
	import InlinePopoverChip from './InlinePopoverChip.svelte';
	import ConditionEditorModal from './ConditionEditorModal.svelte';
	import Tooltip from './Tooltip.svelte';
	import {
		conditionIssue,
		conditionTooltip,
		draftFromRaw,
		summarizeCondition
	} from '$lib/utils/conditionTags';
	import type { VariableRoll, VariablesMap } from '$lib/utils/variableDefs';

	let {
		raw,
		context,
		disabled = false,
		readonly = false,
		checkVariables = true,
		variant = 'default',
		startOpen = false,
		onchange,
		onremove,
		onOpenChange,
		onCreateVariable
	}: {
		raw: string;
		context: Readable<{ variables: VariablesMap; rolls: Record<string, VariableRoll> }>;
		disabled?: boolean;
		readonly?: boolean;
		checkVariables?: boolean;
		variant?: 'default' | 'segment-composer';
		startOpen?: boolean;
		onchange?: (newRaw: string) => void;
		onremove?: () => void;
		onOpenChange?: (open: boolean) => void;
		onCreateVariable?: (name: string) => void;
	} = $props();

	let open = $state(untrack(() => startOpen));
	let current = $state(untrack(() => raw));

	const variables = $derived($context.variables);
	const rolls = $derived($context.rolls);
	const summary = $derived(summarizeCondition(current));
	const issue = $derived(checkVariables ? conditionIssue(current, variables) : null);
	const tooltip = $derived(issue ?? conditionTooltip(current));
	const editable = $derived(draftFromRaw(current) !== null);

	$effect(() => {
		onOpenChange?.(open);
	});

	function handleSave(next: string) {
		if (next !== current) {
			current = next;
			onchange?.(next);
		}
		open = false;
	}

	function handleRemove() {
		onremove?.();
		open = false;
	}
</script>

<InlinePopoverChip
	{variant}
	kind="condition"
	tone={issue ? 'warning' : 'signal'}
	density="tight"
	{disabled}
	{readonly}
	onremove={onremove}
	bind:open
	detached
	canOpen={editable}
	class="condition-chip{issue ? ' broken' : ''}"
	removeTitle="Remove condition"
	popoverLabel="Edit condition"
>
	{#snippet label()}
		<span class="shrink-0 whitespace-nowrap font-mono text-sm {issue ? 'text-warning' : 'text-ai-3'}">if</span>
		{#if summary}
			<Tooltip text={tooltip} wrapperClass="min-w-0 inline-flex items-center gap-1">
			<span class="max-w-[22rem] truncate font-mono text-sm text-fg">{summary.head}</span>
			<span class="text-fg-subtle">&rarr;</span>
			<span class="max-w-[10rem] truncate text-sm text-fg">{summary.then || 'nothing'}</span>
			{#if summary.otherwise !== null}
				<span class="max-w-[9rem] truncate text-sm text-fg-subtle">&middot; else {summary.otherwise || 'nothing'}</span>
			{/if}
			{#if summary.extra > 0}
				<span class="rounded bg-surface-2 px-1 font-mono text-xs tabular-nums text-fg-muted">+{summary.extra}</span>
			{/if}
			</Tooltip>
		{:else}
			<span class="max-w-[22rem] truncate font-mono text-sm text-warning">{current}</span>
		{/if}
		<Icon name="chevron-down" className="w-3 h-3 text-fg-subtle flex-shrink-0" />
	{/snippet}

	{#snippet composerLabel()}
		<span class="chip-mark">if</span>
		{#if summary}
			<Tooltip text={tooltip} wrapperClass="min-w-0 inline-flex items-center">
			<span class="chip-context">{summary.head}</span>
			<span class="chip-label" data-testid="condition-chip-summary">&rarr; {summary.then || 'nothing'}{summary.otherwise !== null ? ` · else ${summary.otherwise || 'nothing'}` : ''}</span>
			{#if summary.extra > 0}
				<span class="chip-state">+{summary.extra}</span>
			{/if}
			</Tooltip>
		{:else}
			<span class="chip-label">{current}</span>
		{/if}
	{/snippet}

</InlinePopoverChip>

{#if open && editable}
	<ConditionEditorModal
		raw={current}
		{variables}
		{rolls}
		{checkVariables}
		onsave={handleSave}
		onremove={onremove ? handleRemove : undefined}
		oncancel={() => (open = false)}
		{onCreateVariable}
	/>
{/if}
