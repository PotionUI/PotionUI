<script lang="ts">
	/** Left rail: searchable, grouped-by-kind node palette. Drag an item onto
	 *  the canvas to add it (handled by `Canvas.svelte`'s drop handler). */
	import { Input } from '$lib/components/ui';
	import { groupedByKind } from '$lib/stores/automationNodeTypes';
	import NodePaletteItem from './NodePaletteItem.svelte';
	import type { NodeKind, NodeTypeDef } from '$lib/types/automations';

	let query = $state('');

	const kindLabels: Record<NodeKind, string> = {
		trigger: 'Triggers',
		condition: 'Conditions',
		action: 'Actions'
	};

	function matches(nodeType: NodeTypeDef, q: string): boolean {
		if (!q) return true;
		const needle = q.toLowerCase();
		return (
			nodeType.title.toLowerCase().includes(needle) ||
			nodeType.key.toLowerCase().includes(needle) ||
			(nodeType.category ?? '').toLowerCase().includes(needle) ||
			(nodeType.description ?? '').toLowerCase().includes(needle)
		);
	}

	function groupByCategory(nodeTypes: NodeTypeDef[]): [string, NodeTypeDef[]][] {
		const groups = new Map<string, NodeTypeDef[]>();
		for (const nt of nodeTypes) {
			const category = nt.category || 'General';
			if (!groups.has(category)) groups.set(category, []);
			groups.get(category)!.push(nt);
		}
		return Array.from(groups.entries());
	}

	let filtered = $derived.by(() => {
		const result: Record<NodeKind, NodeTypeDef[]> = { trigger: [], condition: [], action: [] };
		for (const kind of Object.keys($groupedByKind) as NodeKind[]) {
			result[kind] = $groupedByKind[kind].filter((nt) => matches(nt, query));
		}
		return result;
	});
</script>

<aside class="w-64 flex-shrink-0 bg-surface-1 border-r border-line flex flex-col overflow-hidden">
	<div class="p-3 border-b border-line flex-shrink-0">
		<Input placeholder="Search nodes…" bind:value={query} class="text-sm" />
	</div>

	<div class="flex-1 overflow-y-auto p-3 space-y-4">
		{#each ['trigger', 'condition', 'action'] as kind (kind)}
			{@const nodeTypes = filtered[kind as NodeKind]}
			{#if nodeTypes.length > 0}
				<div>
					<h3 class="text-2xs font-mono font-semibold uppercase tracking-wide text-fg-subtle mb-2">
						{kindLabels[kind as NodeKind]}
					</h3>
					<div class="space-y-3">
						{#each groupByCategory(nodeTypes) as [category, items] (category)}
							{#if items.length > 0}
								<div class="space-y-1.5">
									{#if category !== 'General'}
										<p class="text-2xs text-fg-subtle px-0.5">{category}</p>
									{/if}
									{#each items as nodeType (nodeType.key)}
										<NodePaletteItem {nodeType} />
									{/each}
								</div>
							{/if}
						{/each}
					</div>
				</div>
			{/if}
		{/each}

		{#if Object.values(filtered).every((list) => list.length === 0)}
			<p class="text-xs text-fg-subtle text-center py-6">No node types match "{query}".</p>
		{/if}
	</div>
</aside>
