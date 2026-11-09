<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import type { ToolApprovalRegionRow, ToolApprovalTimelineRow, ToolExecution } from '$lib/types/chat';
	import {
		buildApprovalDiff,
		buildArgumentTree,
		buildDirectorChangeGroups,
		resolveApprovalFieldValue
	} from '$lib/chat/approvalPreview';
	import { loadPresetNameMap } from '$lib/stores/presetsCatalog';
	import type { VariableRoll, VariablesMap } from '$lib/utils/variableDefs';
	import type { NumberVariableSummary } from '$lib/chat/conditionText';
	import ApprovalArgTree from './approval/ApprovalArgTree.svelte';
	import ConditionText from './ConditionText.svelte';
	import NumberVariableValue from './NumberVariableValue.svelte';

	let {
		execution,
		variables = undefined,
		rolls = undefined
	}: { execution: ToolExecution; variables?: VariablesMap; rolls?: Record<string, VariableRoll> } = $props();

	const ITEM_PREVIEW_COUNT = 5;
	const CLAMP_TEXT_LENGTH = 220;

	let presetNames = $state<Record<string, string>>({});
	let itemsExpanded = $state(false);
	let expandedBlocks = $state<Record<number, boolean>>({});

	const preview = $derived(execution.preview ?? null);
	const kind = $derived(preview?.kind ?? null);
	const textBlocks = $derived(kind === 'generation' || kind === 'text_edit' ? (preview?.text_blocks ?? []) : []);
	const fields = $derived(kind === 'generation' || kind === 'text_edit' ? (preview?.fields ?? []) : []);
	const rows = $derived(kind === 'timeline' ? ((preview?.rows ?? []) as ToolApprovalTimelineRow[]) : []);
	const regionRows = $derived(kind === 'regions' ? ((preview?.rows ?? []) as ToolApprovalRegionRow[]) : []);
	const hasTyped = $derived(
		textBlocks.length > 0 || fields.length > 0 || rows.length > 0 || regionRows.length > 0
	);
	const changeGroups = $derived(hasTyped ? null : buildDirectorChangeGroups(preview));
	const diff = $derived(hasTyped || changeGroups ? null : buildApprovalDiff(execution));
	const legacy = $derived(!hasTyped && !changeGroups && !diff && preview?.action ? preview : null);
	const argTree = $derived(!hasTyped && !changeGroups && !diff && !legacy ? buildArgumentTree(execution) : []);
	const legacyItems = $derived(legacy?.items ?? []);
	const shownItems = $derived(itemsExpanded ? legacyItems : legacyItems.slice(0, ITEM_PREVIEW_COUNT));
	const shownGroups = $derived(
		changeGroups ? (itemsExpanded ? changeGroups : changeGroups.slice(0, ITEM_PREVIEW_COUNT)) : []
	);
	const hiddenCount = $derived(
		changeGroups ? changeGroups.length - shownGroups.length : legacyItems.length - shownItems.length
	);
	const needsPresetNames = $derived(fields.some((f) => f.label === 'Preset'));

	$effect(() => {
		if (!needsPresetNames) return;
		let cancelled = false;
		loadPresetNameMap()
			.then((map) => {
				if (!cancelled) presetNames = map;
			})
			.catch((err) => logger.error('Failed to load preset names:', err));
		return () => {
			cancelled = true;
		};
	});

	function fieldValue(field: { label: string; value: string }): string {
		return resolveApprovalFieldValue(field, (id) => presetNames[id] ?? null);
	}

	function toggleBlock(index: number) {
		expandedBlocks = { ...expandedBlocks, [index]: !expandedBlocks[index] };
	}
</script>

{#snippet variableValue(text: string, number: NumberVariableSummary | undefined)}
	{#if number}<NumberVariableValue summary={number} />{:else}{text}{/if}
{/snippet}

<div class="action-preview" data-testid="action-preview">
	{#if fields.length}
		<table class="action-diff">
			<tbody>
				{#each fields as field (field.label)}
					<tr>
						<td class="action-diff-key">{field.label}</td>
						<td>
							{#if field.old != null}<span class="action-old" class:mono={field.mono !== false}>{field.old}</span
								><span class="action-arrow" aria-hidden="true">→</span>{/if}<span
								class="action-new"
								class:mono={field.mono !== false}>{fieldValue(field)}</span
							>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}

	{#each textBlocks as block, i (block.label + i)}
		<div class="action-block">
			<div class="action-block-label">{block.label}</div>
			{#if block.old_text != null}
				<div class="action-copy is-old"><ConditionText text={block.old_text} {variables} {rolls} /></div>
				<div class="action-copy"><ConditionText text={block.text} {variables} {rolls} /></div>
			{:else}
				<div class="action-copy" class:is-clamped={block.text.length > CLAMP_TEXT_LENGTH && !expandedBlocks[i]}>
					<ConditionText text={block.text} {variables} {rolls} />
				</div>
				{#if block.text.length > CLAMP_TEXT_LENGTH}
					<button type="button" class="action-more" onclick={() => toggleBlock(i)}>
						{expandedBlocks[i] ? 'Show less' : 'Show full text'}
					</button>
				{/if}
			{/if}
		</div>
	{/each}

	{#if rows.length}
		<div class="action-timeline">
			{#each rows as row, i (row.range + i)}
				<div class="action-timeline-row">
					<span class="action-range mono">{row.range}</span>
					<span class="action-timeline-text">
						{#if row.op}<span class="action-op">{row.op}</span>{/if}{row.text}
					</span>
				</div>
			{/each}
		</div>
	{/if}

	{#if regionRows.length}
		<div class="action-timeline" data-testid="action-regions">
			{#each regionRows as row, i (row.position + row.size + i)}
				<div class="action-timeline-row">
					<span class="action-range mono">{i + 1}</span>
					<span class="action-timeline-text">
						<span class="action-range mono">{row.position} · {row.size}</span>
						<span class="block">{row.text}</span>
					</span>
				</div>
			{/each}
		</div>
	{/if}

	{#if changeGroups}
		{#each shownGroups as group, i (group.op + i)}
			<div class="action-group">
				<div class="action-group-head">
					<span class="action-op is-{group.kind}">{group.kind}</span>
					<span>{group.summary}</span>
				</div>
				{#if group.rows.length}
					<table class="action-diff">
						<tbody>
							{#each group.rows as row (row.field)}
								<tr>
									<td class="action-diff-key mono">{row.field}</td>
									<td>
										{#if group.kind !== 'add'}<span class="action-old">{row.oldValue}</span
											><span class="action-arrow" aria-hidden="true">→</span>{/if}<span class="action-new"
											>{row.newValue}</span
										>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				{/if}
			</div>
		{/each}
	{:else if diff}
		<table class="action-diff">
			<tbody>
				{#each diff as row (row.field)}
					<tr>
						<td class="action-diff-key">{row.field}</td>
						<td>
							<span class="action-old" class:mono={!row.oldNumber}
								>{@render variableValue(row.oldValue, row.oldNumber)}</span
							><span class="action-arrow" aria-hidden="true">→</span
							><span class="action-new" class:mono={!row.newNumber}
								>{@render variableValue(row.newValue, row.newNumber)}</span
							>
							{#if row.reason}<div class="action-reason-note">{row.reason}</div>{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{:else if legacy}
		<div class="action-legacy">
			<strong>{legacy.action}</strong>{#if legacy.target}<span class="ml-1">{legacy.target}</span>{/if}
		</div>
		{#if legacyItems.length}
			<div class="action-items">
				{#each shownItems as item, i (item + i)}
					<span class="action-item">{item}</span>
				{/each}
			</div>
		{/if}
		{#if legacy.note}
			<div class="action-reason-note">{legacy.note}</div>
		{/if}
	{:else if argTree.length}
		<ApprovalArgTree nodes={argTree} />
	{/if}

	{#if hiddenCount > 0 || itemsExpanded}
		<button type="button" class="action-more" onclick={() => (itemsExpanded = !itemsExpanded)}>
			{itemsExpanded ? 'Show less' : `+${hiddenCount} more`}
		</button>
	{/if}
</div>
