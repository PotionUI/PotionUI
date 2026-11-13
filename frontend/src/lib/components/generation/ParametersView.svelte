<script lang="ts">
	import { CopyButton, Spinner } from '$lib/components/ui';
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
						class="m-0 grid gap-x-6 gap-y-3"
						style="grid-template-columns: repeat({columns}, minmax(0, 1fr))"
						data-columns={columns}
					>
						{#each fold.visible as row (row.key)}
							<div class="group min-w-0 {row.wide ? 'col-span-full' : ''}" data-param-cell>
								<dt class="flex min-h-5 min-w-0 items-center justify-between gap-1 text-sm text-fg-muted">
									<span class="flex min-w-0 items-center">
										<span>{row.label}</span>
										{#if axisTags[row.key]}
											<span
												data-testid="axis-tag"
												class="ml-1.5 rounded border border-signal/40 bg-signal/10 px-1 font-mono text-sm text-signal"
											>
												{axisTags[row.key]}
											</span>
										{/if}
									</span>
									<CopyButton
										text={row.raw}
										ariaLabel="Copy {row.label}"
										size="xs"
										class="-my-1 opacity-0 group-focus-within:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100"
									/>
								</dt>
								<dd
									class="m-0 min-w-0 text-md [overflow-wrap:anywhere] {row.mono
										? 'font-mono tabular-nums'
										: ''} {row.off ? 'text-fg-muted' : 'text-fg'}"
								>
									{row.display}{#if row.unit}{row.unitSpaced ? ' ' : ''}<span class="text-fg-muted">{row.unit}</span>{/if}
								</dd>
							</div>
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
