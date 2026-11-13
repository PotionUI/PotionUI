<script lang="ts">
	import { Spinner } from '$lib/components/ui';
	import DetailRow from './DetailRow.svelte';
	import FoldButton from './FoldButton.svelte';
	import ParameterTextBlock from './ParameterTextBlock.svelte';
	import {
		buildParameterView,
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

	let view = $derived(buildParameterView({ params, meta, order, models }));
	let fold = $derived(foldShortRows(view.shorts, $parametersExpanded));
	let isEmpty = $derived(view.longs.length === 0 && view.shorts.length === 0);
</script>

<div class="params-view {className}" data-testid="parameters-view">
	{#if loading}
		<div class="flex items-center justify-center py-4">
			<Spinner size="sm" />
		</div>
	{:else if isEmpty}
		<p class="p-3 text-sm text-fg-subtle">{emptyText}</p>
	{:else}
		<div class="divide-y divide-line">
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
				<div>
					<dl id={listId} class="facts m-0 px-3 pb-2 pt-1">
						{#each fold.visible as row (row.key)}
							<DetailRow
								label={row.label}
								copy={row.raw}
								mono={row.mono}
								muted={row.off}
							>
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
								{row.display}{#if row.unit}{row.unitSpaced ? ' ' : ''}<span class="text-fg-muted">{row.unit}</span>{/if}
							</DetailRow>
						{/each}
					</dl>
					{#if fold.foldable}
						<div class="px-3 pb-2">
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

<style>
	.params-view {
		container-type: inline-size;
	}
	.facts {
		columns: 1;
		column-gap: 24px;
	}
	@container (min-width: 560px) {
		.facts {
			columns: 2;
		}
	}
</style>
