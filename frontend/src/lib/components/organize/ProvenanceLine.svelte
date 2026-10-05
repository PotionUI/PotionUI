<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { api } from '$lib/services/api/index';
	import { organizeHref } from '$lib/organize/subjects';
	import { groupProvenance, type ProvenanceGroup } from '$lib/organize/provenance';
	import type { OrganizeSubject } from '$lib/types/organize';

	let {
		itemType,
		itemId,
		class: className = ''
	}: {
		itemType: OrganizeSubject;
		itemId: string;
		class?: string;
	} = $props();

	let groups = $state<ProvenanceGroup[]>([]);

	$effect(() => {
		const type = itemType;
		const id = itemId;
		let cancelled = false;
		groups = [];
		if (!id) return;
		api
			.getOrganizeProvenance(type, id)
			.then((response) => {
				if (cancelled) return;
				groups = response.success && Array.isArray(response.data) ? groupProvenance(response.data) : [];
			})
			.catch(() => {
				if (!cancelled) groups = [];
			});
		return () => {
			cancelled = true;
		};
	});
</script>

{#if groups.length > 0}
	<ul class="space-y-1 {className}" data-testid="provenance-line">
		{#each groups as group (group.ruleId)}
			<li class="flex flex-wrap items-center gap-1.5 text-xs text-fg-muted">
				<Icon name="sparkles" className="h-3 w-3 flex-shrink-0 text-fg-subtle" />
				{#if group.deleted}
					<span>Added by a deleted rule</span>
				{:else}
					<span>Added by rule</span>
					<a class="font-medium text-signal hover:underline" href={organizeHref(itemType, { rule: group.ruleId })}>
						{group.ruleName}
					</a>
				{/if}
				{#if group.targets.length > 0}
					<span class="text-fg-subtle">{group.targets.join(', ')}</span>
				{/if}
			</li>
		{/each}
	</ul>
{/if}
