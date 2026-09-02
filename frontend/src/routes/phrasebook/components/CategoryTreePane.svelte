<script lang="ts">
	import { Pane, PaneTree, PaneRow, type PaneTreeNode } from '$lib/components/pane';
	import { phrasebookStore, type CategoryWithChildren } from '$lib/stores/phrasebook';

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
</script>

<div class="flex-shrink-0 border-r border-line flex flex-col bg-surface-1" style="width: {width}px">
	<Pane
		label="Categories"
		count={current.rootCategoryIds.length}
		loading={current.isLoading && current.rootCategoryIds.length === 0}
		isEmpty={current.rootCategoryIds.length === 0}
		bodyPadding="sm"
		bodyRole="tree"
	>
		{#snippet empty()}
			<div class="text-center py-8 px-4">
				<p class="text-sm text-fg-subtle">No categories</p>
			</div>
		{/snippet}

		{#snippet children()}
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
		{/snippet}
	</Pane>
</div>
