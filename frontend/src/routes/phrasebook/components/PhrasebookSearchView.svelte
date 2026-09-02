<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import { parseComponentRef } from '$lib/plugin-api/componentRef';
	import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Badge, EmptyState, IconButton, Spinner } from '$lib/components/ui';
	import { api } from '$lib/services/api/index';
	import { confirmDialog } from '$lib/stores/confirm';
	import { phrasebookStore } from '$lib/stores/phrasebook';
	import { toasts } from '$lib/stores/toast';
	import { logger } from '$lib/utils/logger';
	import type {
		PhrasebookBatchOp,
		PhrasebookBatchOutcome,
		PhrasebookFindResult,
		PhrasebookFindValueHit
	} from '$lib/types/api';
	import {
		apiErrorDetail,
		highlightSegments,
		pluginBatchOps,
		rangeIds,
		retainSelection,
		toggleAll,
		toggleId,
		type FindFilters
	} from '../phrasebookSearch';
	import PhrasebookReplaceModal from './PhrasebookReplaceModal.svelte';
	import PhrasebookSelectionBar from './PhrasebookSelectionBar.svelte';
	import ValueEditForm from './ValueEditForm.svelte';

	let {
		result,
		loading = false,
		filters,
		onClearQuery,
		onRerun
	}: {
		result: PhrasebookFindResult | null;
		loading?: boolean;
		filters: FindFilters;
		onClearQuery: () => void;
		onRerun: () => Promise<void> | void;
	} = $props();

	let values = $derived(result?.values ?? []);
	let categories = $derived(filters.scope === 'values' ? [] : (result?.categories ?? []));
	let orderedIds = $derived(values.map((v) => v.id));
	let showValues = $derived(filters.scope !== 'categories');
	let isEmpty = $derived(!!result && values.length === 0 && categories.length === 0);

	let selected = $state<Set<string>>(new Set());
	let anchorId: string | null = null;
	let busy = $state(false);
	let replaceOpen = $state(false);
	let editOpen = $state(false);
	let batchOps = $state<PhrasebookBatchOp[]>([]);
	let extraOps = $derived(pluginBatchOps(batchOps));
	let activePluginOp = $state<PhrasebookBatchOp | null>(null);
	let pluginOpIds = $state<string[]>([]);
	let pluginRef = $derived(activePluginOp ? parseComponentRef(activePluginOp.component) : null);

	onMount(async () => {
		try {
			const response = await api.listPhrasebookBatchOps();
			if (response.success && response.data) batchOps = response.data;
		} catch (e) {
			logger.error('Failed to load phrasebook batch operations:', e);
		}
	});

	let selectedValues = $derived(values.filter((v) => selected.has(v.id)));
	let allSelected = $derived(orderedIds.length > 0 && orderedIds.every((id) => selected.has(id)));
	let someSelected = $derived(selected.size > 0 && !allSelected);

	$effect(() => {
		const ids = orderedIds;
		const current = untrack(() => selected);
		const retained = retainSelection(current, ids);
		if (retained.size !== current.size) selected = retained;
	});

	$effect(() => {
		if (editOpen && $phrasebookStore.editMode === 'none') {
			editOpen = false;
			onRerun();
		}
	});

	function indeterminate(node: HTMLInputElement, value: boolean) {
		node.indeterminate = value;
		return {
			update(next: boolean) {
				node.indeterminate = next;
			}
		};
	}

	function handleRowCheck(e: MouseEvent, id: string) {
		if (e.shiftKey && anchorId) {
			const range = rangeIds(orderedIds, anchorId, id);
			selected = new Set([...selected, ...range]);
		} else {
			selected = toggleId(selected, id);
		}
		anchorId = id;
	}

	let tableEl: HTMLTableElement | undefined = $state();

	function handleWindowKeydown(e: KeyboardEvent) {
		if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 'a') return;
		if (!tableEl || !tableEl.contains(document.activeElement)) return;
		e.preventDefault();
		selected = new Set(orderedIds);
	}

	async function revealCategory(id: string) {
		await phrasebookStore.revealCategory(id);
		onClearQuery();
	}

	async function showInTree(hit: PhrasebookFindValueHit) {
		await phrasebookStore.revealValue(hit.category_id, hit.id);
		onClearQuery();
	}

	async function openEditor(hit: PhrasebookFindValueHit) {
		await phrasebookStore.revealValue(hit.category_id, hit.id);
		editOpen = true;
	}

	function closeEditor() {
		editOpen = false;
		phrasebookStore.handleCancelEdit();
	}

	function affectedCategoryIds(ids: string[]): string[] {
		const wanted = new Set(ids);
		return values.filter((v) => wanted.has(v.id)).map((v) => v.category_id);
	}

	async function finishBatch(message: string, affected: string[]) {
		toasts.success(message);
		await phrasebookStore.reloadCategoryValuesIfLoaded(affected);
		await onRerun();
	}

	function reportFailure(e: unknown, fallback: string) {
		const detail = apiErrorDetail(e);
		toasts.error(detail?.message ?? fallback);
		if (!detail) logger.error(fallback, e);
	}

	async function setActive(isActive: boolean) {
		const ids = [...selected];
		if (ids.length === 0 || busy) return;
		busy = true;
		try {
			const response = await api.runPhrasebookBatch('set_active', ids, { is_active: isActive });
			if (!response.success || !response.data) throw new Error(response.message || response.error || 'Update failed');
			await finishBatch(response.data.message, affectedCategoryIds(ids));
		} catch (e) {
			reportFailure(e, 'Could not update the selected values');
		} finally {
			busy = false;
		}
	}

	async function moveTo(categoryId: string) {
		const ids = [...selected];
		if (ids.length === 0 || busy) return;
		busy = true;
		try {
			const response = await api.runPhrasebookBatch('move', ids, { category_id: categoryId });
			if (!response.success || !response.data) throw new Error(response.message || response.error || 'Move failed');
			await finishBatch(response.data.message, [...affectedCategoryIds(ids), categoryId]);
		} catch (e) {
			reportFailure(e, 'Could not move the selected values');
		} finally {
			busy = false;
		}
	}

	async function deleteSelected() {
		const ids = [...selected];
		if (ids.length === 0 || busy) return;
		const ok = await confirmDialog({
			title: 'Delete values',
			message: `Delete ${ids.length} value${ids.length === 1 ? '' : 's'}? This cannot be undone.`,
			variant: 'danger'
		});
		if (!ok) return;
		busy = true;
		try {
			const response = await api.runPhrasebookBatch('delete', ids, {});
			if (!response.success || !response.data) throw new Error(response.message || response.error || 'Delete failed');
			const affected = affectedCategoryIds(ids);
			selected = new Set();
			await finishBatch(response.data.message, affected);
		} catch (e) {
			reportFailure(e, 'Could not delete the selected values');
		} finally {
			busy = false;
		}
	}

	async function handleReplaced(outcome: PhrasebookBatchOutcome) {
		replaceOpen = false;
		await finishBatch(outcome.message, outcome.updated.map((v) => v.category_id));
	}

	async function runOp(op: PhrasebookBatchOp) {
		const ids = [...selected];
		if (ids.length === 0 || busy) return;
		if (op.component) {
			pluginOpIds = ids;
			activePluginOp = op;
			return;
		}
		busy = true;
		try {
			const response = await api.runPhrasebookBatch(op.id, ids, {});
			if (!response.success || !response.data) throw new Error(response.message || response.error || `${op.label} failed`);
			await finishBatch(response.data.message || `${op.label} done`, affectedCategoryIds(ids));
		} catch (e) {
			reportFailure(e, `${op.label} failed`);
		} finally {
			busy = false;
		}
	}

	function closePluginOp() {
		activePluginOp = null;
	}

	async function handlePluginDone(outcome?: Partial<PhrasebookBatchOutcome>) {
		const op = activePluginOp;
		const ids = pluginOpIds;
		activePluginOp = null;
		await finishBatch(outcome?.message || `${op?.label ?? 'Operation'} done`, affectedCategoryIds(ids));
	}
</script>

<svelte:window onkeydown={handleWindowKeydown} />

<div class="flex-1 min-h-0 flex flex-col bg-canvas" data-search-view>
	<div class="flex items-center gap-3 px-4 py-2 border-b border-line bg-surface-1/50 flex-shrink-0">
		<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted tabular-nums" data-search-counts>
			{#if result}
				{#if showValues}<span class="text-fg">{result.total_values}</span> values{/if}
				{#if showValues && filters.scope === 'all'} · {/if}
				{#if filters.scope !== 'values'}<span class="text-fg">{result.total_categories}</span> categories{/if}
				{#if result.total_values > values.length || result.total_categories > (result.categories?.length ?? 0)}
					<span class="ml-2 text-fg-subtle normal-case tracking-normal">showing the first {Math.max(values.length, result.categories.length)}</span>
				{/if}
			{:else}
				Searching…
			{/if}
		</span>
		{#if loading}<Spinner size="sm" />{/if}
	</div>

	<div class="flex-1 min-h-0 overflow-y-auto">
		{#if isEmpty}
			<div class="p-6 max-w-lg mx-auto">
				<EmptyState compact icon="search" title="Nothing matches" description="Try another mode, scope or spelling." />
			</div>
		{:else if result}
			{#if categories.length > 0}
				<section class="px-4 pt-4" data-search-categories>
					<h2 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-2">Categories</h2>
					<ul class="rounded-lg border border-line bg-surface-1 divide-y divide-line">
						{#each categories as category (category.id)}
							<li>
								<button
									type="button"
									class="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-surface-2 transition-colors {category.is_active ? '' : 'opacity-70'}"
									data-category-hit={category.id}
									onclick={() => revealCategory(category.id)}
								>
									<Icon name="folder" className="w-4 h-4 text-fg-subtle flex-shrink-0" />
									<span class="text-sm text-fg truncate">
										{#each highlightSegments(category.name, category.matches, 'name') as part}{#if part.match}<mark>{part.text}</mark>{:else}{part.text}{/if}{/each}
									</span>
									<span class="font-mono text-2xs text-fg-subtle truncate">
										{#each highlightSegments(category.path, category.matches, 'path') as part}{#if part.match}<mark>{part.text}</mark>{:else}{part.text}{/if}{/each}
									</span>
									{#if !category.is_active}<Badge size="sm">inactive</Badge>{/if}
									<span class="flex-1"></span>
									<Icon name="chevron-right" className="w-4 h-4 text-fg-subtle flex-shrink-0" />
								</button>
							</li>
						{/each}
					</ul>
				</section>
			{/if}

			{#if showValues && values.length > 0}
				<section class="px-4 py-4" data-search-values>
					<h2 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-2">Values</h2>
					<div class="rounded-lg border border-line bg-surface-1 overflow-x-auto">
						<table class="w-full text-sm" data-search-table bind:this={tableEl}>
							<thead class="sticky top-0 bg-surface-2 text-left">
								<tr class="border-b border-line">
									<th class="w-8 px-3 py-2">
										<input
											type="checkbox"
											class="accent-accent"
											aria-label="Select all values"
											checked={allSelected}
											use:indeterminate={someSelected}
											onchange={() => (selected = toggleAll(selected, orderedIds))}
										/>
									</th>
									<th class="px-3 py-2 font-mono text-2xs uppercase tracking-[0.07em] font-medium text-fg-subtle">Label</th>
									<th class="px-3 py-2 font-mono text-2xs uppercase tracking-[0.07em] font-medium text-fg-subtle">Value</th>
									<th class="px-3 py-2 font-mono text-2xs uppercase tracking-[0.07em] font-medium text-fg-subtle">Category</th>
									<th class="px-3 py-2 font-mono text-2xs uppercase tracking-[0.07em] font-medium text-fg-subtle">Active</th>
									<th class="w-20 px-3 py-2"></th>
								</tr>
							</thead>
							<tbody class="divide-y divide-line">
								{#each values as hit (hit.id)}
									<tr
										class="{selected.has(hit.id) ? 'bg-signal/5' : 'hover:bg-surface-2/60'} {hit.is_active ? '' : 'text-fg-muted'}"
										data-value-row={hit.id}
										aria-selected={selected.has(hit.id)}
									>
										<td class="px-3 py-2 align-top">
											<input
												type="checkbox"
												class="accent-accent"
												aria-label="Select {hit.label}"
												checked={selected.has(hit.id)}
												onclick={(e) => handleRowCheck(e, hit.id)}
											/>
										</td>
										<td class="px-3 py-2 align-top font-medium text-fg max-w-[16rem] truncate" title={hit.label}>
											{#each highlightSegments(hit.label, hit.matches, 'label') as part}{#if part.match}<mark>{part.text}</mark>{:else}{part.text}{/if}{/each}
										</td>
										<td class="px-3 py-2 align-top font-mono text-xs max-w-[36rem] truncate" title={hit.value}>
											{#each highlightSegments(hit.value, hit.matches, 'value') as part}{#if part.match}<mark>{part.text}</mark>{:else}{part.text}{/if}{/each}
										</td>
										<td class="px-3 py-2 align-top font-mono text-2xs text-fg-subtle max-w-[14rem] truncate" title={hit.category_path}>
											{hit.category_path}
											{#if !hit.category_is_active}<Badge size="sm" class="ml-1">inactive category</Badge>{/if}
										</td>
										<td class="px-3 py-2 align-top">
											{#if hit.is_active}
												<Badge size="sm" variant="success">active</Badge>
											{:else}
												<Badge size="sm">inactive</Badge>
											{/if}
										</td>
										<td class="px-2 py-1 align-top">
											<div class="flex items-center justify-end gap-0.5">
												<Tooltip text="Edit value" position="left">
													<IconButton icon="edit" label="Edit value" size="sm" onclick={() => openEditor(hit)} />
												</Tooltip>
												<Tooltip text="Show in tree" position="left">
													<IconButton icon="list-tree" label="Show in tree" size="sm" onclick={() => showInTree(hit)} />
												</Tooltip>
											</div>
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</section>
			{/if}
		{/if}
	</div>
</div>

<PhrasebookSelectionBar
	selectedCount={selected.size}
	totalCount={orderedIds.length}
	{busy}
	categories={$phrasebookStore.allCategories}
	{extraOps}
	selectedIds={[...selected]}
	onRunOp={runOp}
	onSelectAll={() => (selected = new Set(orderedIds))}
	onClear={() => (selected = new Set())}
	onReplace={() => (replaceOpen = true)}
	onSetActive={setActive}
	onMove={moveTo}
	onDelete={deleteSelected}
/>

<PhrasebookReplaceModal
	isOpen={replaceOpen}
	values={selectedValues}
	{filters}
	onClose={() => (replaceOpen = false)}
	onApplied={handleReplaced}
/>

<BaseModal isOpen={!!activePluginOp} title={activePluginOp?.label ?? ''} size="lg" on:close={closePluginOp}>
	<div class="p-4 md:p-6" data-plugin-op-modal>
		{#if activePluginOp && pluginRef}
			{#await resolvePluginComponent(pluginRef.pluginId, pluginRef.asset)}
				<div class="flex items-center justify-center py-8"><Spinner size="sm" /></div>
			{:then Component}
				{#if Component}
					<Component valueIds={pluginOpIds} onClose={closePluginOp} onDone={handlePluginDone} />
				{:else}
					<p class="text-sm text-danger">Could not load the plugin component for {activePluginOp.label}.</p>
				{/if}
			{/await}
		{:else if activePluginOp}
			<p class="text-sm text-danger">{activePluginOp.label} has no loadable component.</p>
		{/if}
	</div>
</BaseModal>

<BaseModal isOpen={editOpen} title="" size="lg" hideCloseButton on:close={closeEditor}>
	<div class="h-[70vh] flex flex-col" data-edit-modal>
		{#if editOpen}
			<ValueEditForm />
		{/if}
	</div>
</BaseModal>

<style>
	mark {
		background: rgb(var(--signal) / 0.22);
		color: inherit;
		border-radius: 2px;
		padding: 0 1px;
	}
</style>
