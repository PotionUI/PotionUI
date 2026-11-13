<script lang="ts">
	import { Spinner } from '$lib/components/ui';
	import ValueBox from './ValueBox.svelte';
	import ParameterTextBlock from './ParameterTextBlock.svelte';
	import FoldButton from './FoldButton.svelte';
	import {
		buildParameterView,
		columnsForWidth,
		foldShortRows,
		type ParameterMeta,
		type ParameterModelRef
	} from './parametersModel';
	import { loadFoldPreference, parametersExpanded, setFoldPreference } from './parametersFold';

	let {
		params = {},
		meta = null,
		order = null,
		models = [],
		axisTags = {},
		loading = false,
		emptyText = 'No parameters',
		class: className = ''
	}: {
		params?: Record<string, unknown> | null;
		meta?: Record<string, ParameterMeta> | null;
		order?: string[] | null;
		models?: ParameterModelRef[] | null;
		axisTags?: Record<string, string>;
		loading?: boolean;
		emptyText?: string;
		class?: string;
	} = $props();

	const uid = $props.id();
	const listId = `${uid}-parameters`;

	loadFoldPreference();

	let width = $state(0);
	let columns = $derived(columnsForWidth(width));
	let view = $derived(buildParameterView({ params, meta, order, models }));
	let fold = $derived(foldShortRows(view.shorts, $parametersExpanded, columns));
	let isEmpty = $derived(view.longs.length === 0 && view.shorts.length === 0);
</script>

<div class="params-view {className}" data-testid="parameters-view" bind:clientWidth={width}>
	{#if loading}
		<div class="flex items-center justify-center py-4">
			<Spinner size="sm" />
		</div>
	{:else if isEmpty}
		<p class="p-3 text-sm text-fg-subtle">{emptyText}</p>
	{:else}
		<div>
			{#each view.longs as row (row.key)}
				<ParameterTextBlock
					label={row.label}
					text={row.text}
					raw={row.raw}
					mono={row.mono}
					axisTag={axisTags[row.key] ?? ''}
				/>
			{/each}
			{#if view.shorts.length > 0}
				<div class="px-3 pb-2 pt-1">
					<dl
						id={listId}
						class="m-0 grid gap-2"
						style="grid-template-columns: repeat({columns}, minmax(0, 1fr))"
						data-columns={columns}
					>
						{#each fold.visible as row (row.key)}
							<ValueBox label={row.label} raw={row.raw} mono={row.mono} span={Math.min(row.span, columns)}>
								{#snippet labelExtra()}
									{#if axisTags[row.key]}
										<span
											data-testid="axis-tag"
											class="ml-1.5 rounded border border-signal/40 bg-signal/10 px-1 font-mono text-sm text-signal"
										>
											{axisTags[row.key]}
										</span>
									{/if}
								{/snippet}
								{row.display}{#if row.unit}<span class="font-normal text-fg-muted">{row.unitSpaced ? ' ' : ''}{row.unit}</span>{/if}
							</ValueBox>
						{/each}
					</dl>
					{#if fold.foldable}
						<div class="pt-1">
							<FoldButton
								label={$parametersExpanded ? 'Show less' : `Show all (${fold.total})`}
								expanded={$parametersExpanded}
								controls={listId}
								onToggle={() => setFoldPreference(!$parametersExpanded)}
							/>
						</div>
					{/if}
				</div>
			{/if}
		</div>
	{/if}
</div>
