<script lang="ts">
	import { tick } from 'svelte';
	import { Pane, PaneTree, PaneRow, PaneGroupHeader, type PaneTreeNode } from '$lib/components/pane';
	import { Badge, Button, EmptyState, IconButton, Input, Spinner } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { api } from '$lib/services/api/index';
	import { logger } from '$lib/utils/logger';
	import type { PhrasebookFindResult, PhrasebookFindValueHit } from '$lib/types/api';
	import { phrasebookStore, type CategoryWithChildren } from '$lib/stores/phrasebook';
	import {
		isSearching,
		splitHighlight,
		excerpt,
		groupFindResults,
		emptyFindResult
	} from '../phrasebookFind';

	// Self-contained: reads/writes phrasebookStore directly. Extracted
	// verbatim from phrasebook/+page.svelte (left category tree pane).
	let { width }: { width: number } = $props();

	let current = $derived($phrasebookStore);

	// Lazy tree: a category's `children` array is only populated once it has
	// been expanded (phrasebookStore.loadCategoryChildren), so this walks
	// whatever is already loaded rather than a flat parent_id list.
	// `categories[ref.id]` is preferred over `ref` itself since it's the
	// canonical, freshest copy (matches the old TreeNode consumer's fallback).
	function buildNodes(
		refs: CategoryWithChildren[],
		depth: number
	): PaneTreeNode<CategoryWithChildren>[] {
		return refs.map((ref) => {
			const cat = current.categories[ref.id] ?? ref;
			return {
				item: cat,
				children: buildNodes(cat.children ?? [], depth + 1),
				depth
			};
		});
	}

	let rootNodes = $derived(
		buildNodes(
			current.rootCategoryIds
				.map((id) => current.categories[id])
				.filter((c): c is CategoryWithChildren => !!c),
			0
		)
	);

	let paneEl: HTMLDivElement | undefined = $state();
	let search = $state('');
	let searching = $state(false);
	let results = $state<PhrasebookFindResult | null>(null);
	let requestSeq = 0;

	let showResults = $derived(isSearching(search));
	let groups = $derived(results ? groupFindResults(results) : null);
	let highlightQuery = $derived(results?.query ?? search);

	async function runFind(query: string) {
		const seq = ++requestSeq;
		if (!isSearching(query)) {
			results = null;
			searching = false;
			editingId = null;
			return;
		}
		searching = true;
		try {
			const response = await api.findPhrasebook(query.trim());
			if (seq !== requestSeq) return;
			results = response.success && response.data ? response.data : emptyFindResult(query);
		} catch (error) {
			if (seq !== requestSeq) return;
			logger.error('Phrasebook search failed:', error);
			results = emptyFindResult(query);
		} finally {
			if (seq === requestSeq) searching = false;
		}
	}

	function handleGlobalKeydown(e: KeyboardEvent) {
		if (e.key !== '/') return;
		const target = e.target as HTMLElement | null;
		const tag = target?.tagName;
		if (tag === 'INPUT' || tag === 'TEXTAREA' || target?.isContentEditable) return;
		e.preventDefault();
		paneEl?.querySelector<HTMLInputElement>('input[type="text"]')?.focus();
	}

	let editingId = $state<string | null>(null);
	let editingText = $state('');
	let saving = $state(false);
	let saveFailed = $state(false);

	async function startEdit(hit: PhrasebookFindValueHit) {
		editingId = hit.id;
		editingText = hit.value;
		saveFailed = false;
		await tick();
		paneEl?.querySelector<HTMLInputElement>('[data-quick-edit] input')?.focus();
	}

	function cancelEdit() {
		editingId = null;
		saveFailed = false;
	}

	async function saveEdit(hit: PhrasebookFindValueHit) {
		const text = editingText.trim();
		if (!text || saving) return;
		if (text === hit.value) {
			cancelEdit();
			return;
		}
		saving = true;
		saveFailed = false;
		const ok = await phrasebookStore.updateValueText(hit, text);
		saving = false;
		if (!ok) {
			saveFailed = true;
			return;
		}
		if (results) {
			results = {
				...results,
				values: results.values.map((v) => (v.id === hit.id ? { ...v, value: text } : v))
			};
		}
		editingId = null;
	}

	function handleEditKeydown(e: KeyboardEvent, hit: PhrasebookFindValueHit) {
		e.stopPropagation();
		if (e.key === 'Enter') {
			e.preventDefault();
			saveEdit(hit);
		} else if (e.key === 'Escape') {
			e.preventDefault();
			cancelEdit();
		}
	}
</script>

<svelte:window onkeydown={handleGlobalKeydown} />

{#snippet highlighted(text: string)}
	{#each splitHighlight(text, highlightQuery) as part}
		{#if part.match}<mark>{part.text}</mark>{:else}{part.text}{/if}
	{/each}
{/snippet}

<div
	bind:this={paneEl}
	class="flex-shrink-0 border-r border-line flex flex-col bg-surface-1"
	style="width: {width}px"
>
	<Pane
		label={showResults ? 'Search' : 'Categories'}
		count={showResults ? groups?.total : current.rootCategoryIds.length}
		searchable
		bind:search
		searchPlaceholder="Search categories and values"
		onSearch={runFind}
		searchDebounceMs={200}
		loading={!showResults && current.isLoading && current.rootCategoryIds.length === 0}
		isEmpty={showResults ? !!groups && groups.total === 0 : current.rootCategoryIds.length === 0}
		bodyPadding="sm"
		bodyRole={showResults ? 'listbox' : 'tree'}
	>
		{#snippet headerActions()}
			{#if searching}
				<Spinner size="sm" />
			{/if}
		{/snippet}

		{#snippet empty()}
			{#if showResults}
				<div class="p-2">
					<EmptyState compact icon="search" title="Nothing matches" />
				</div>
			{:else}
				<div class="text-center py-8 px-4">
					<p class="text-sm text-fg-subtle">No categories</p>
				</div>
			{/if}
		{/snippet}

		{#snippet children()}
			{#if showResults}
				{#if groups}
					<PaneGroupHeader icon="folder" label="Categories" count={groups.categories.count} />
					{#each groups.categories.items as category (category.id)}
						<PaneRow
							size="sm"
							role="option"
							icon="folder"
							selected={category.id === current.selectedCategoryId}
							inactive={!category.is_active}
							onclick={() => phrasebookStore.selectCategoryFromFind(category.id)}
						>
							<div class="flex items-center gap-1.5 min-w-0">
								<span class="truncate">{@render highlighted(category.name)}</span>
								{#if !category.is_active}<Badge size="sm">inactive</Badge>{/if}
							</div>
							<div class="truncate font-mono text-2xs text-fg-subtle">
								{@render highlighted(category.path)}
							</div>
						</PaneRow>
					{:else}
						<p class="px-3 py-2 text-xs text-fg-subtle">No categories</p>
					{/each}

					<PaneGroupHeader icon="tag" label="Values" count={groups.values.count} />
					{#each groups.values.items as hit (hit.id)}
						{#if editingId === hit.id}
							<div
								data-quick-edit
								class="mx-1 my-1 p-2 rounded border border-line bg-surface-2 space-y-2"
								role="group"
								aria-label="Edit value text"
							>
								<div class="truncate text-xs font-medium text-fg">{hit.label}</div>
								<Input
									bind:value={editingText}
									class="font-mono text-xs"
									placeholder="Value text"
									aria-label="Value text"
									disabled={saving}
									onkeydown={(e: KeyboardEvent) => handleEditKeydown(e, hit)}
								/>
								{#if saveFailed}
									<p class="text-xs text-danger">Could not save</p>
								{/if}
								<div class="flex items-center justify-end gap-1">
									<Button size="xs" variant="ghost" onclick={cancelEdit} disabled={saving}>Cancel</Button>
									<Button
										size="xs"
										variant="primary"
										onclick={() => saveEdit(hit)}
										disabled={saving || !editingText.trim()}
									>
										Save
									</Button>
								</div>
							</div>
						{:else}
							{#snippet actions()}
								<Tooltip text="Edit value" position="left">
									<IconButton
										icon="edit"
										label="Edit value"
										size="sm"
										onclick={(e) => {
											e.stopPropagation();
											startEdit(hit);
										}}
									/>
								</Tooltip>
							{/snippet}
							<PaneRow
								size="sm"
								role="option"
								icon="tag"
								selected={hit.id === current.selectedValueId}
								inactive={!hit.is_active}
								onclick={() => phrasebookStore.selectValueFromFind(hit.category_id, hit.id)}
								{actions}
							>
								<div class="flex items-center gap-1.5 min-w-0">
									<span class="truncate">{@render highlighted(hit.label)}</span>
									{#if !hit.is_active}<Badge size="sm">inactive</Badge>{/if}
								</div>
								<div class="truncate font-mono text-2xs text-fg-subtle">
									{@render highlighted(hit.category_path)}
								</div>
								<div class="truncate font-mono text-2xs text-fg-muted">
									{@render highlighted(excerpt(hit.value, highlightQuery))}
								</div>
							</PaneRow>
						{/if}
					{:else}
						<p class="px-3 py-2 text-xs text-fg-subtle">No values</p>
					{/each}
				{/if}
			{:else}
				<PaneTree
					nodes={rootNodes}
					expanded={current.expandedCategories}
					onToggle={(id) => phrasebookStore.handleToggleCategory(id)}
					hasChildren={(node) => phrasebookStore.hasChildren(node.item.id)}
				>
					{#snippet row({ item, depth, hasChildren, expanded, toggle })}
						<PaneRow
							size="sm"
							role="treeitem"
							{depth}
							expandable={hasChildren}
							{expanded}
							onToggle={toggle}
							loading={current.loadingCategories.has(item.id)}
							selected={item.id === current.selectedCategoryId}
							inactive={!item.is_active}
							inactiveBadge="OFF"
							icon="folder"
							title={item.name}
							onclick={() => phrasebookStore.handleSelectCategory(item.id)}
						/>
					{/snippet}
				</PaneTree>
			{/if}
		{/snippet}
	</Pane>
</div>

<style>
	mark {
		background: rgb(var(--signal) / 0.22);
		color: inherit;
		border-radius: 2px;
		padding: 0 1px;
	}
</style>
