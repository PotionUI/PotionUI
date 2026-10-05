<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { api } from '$lib/services/api/index';
	import { collectionRulesHref, filledByLabel } from '$lib/organize/collectionRules';
	import type { OrganizeCollectionRuleRef, OrganizeCollectionScope } from '$lib/types/organize';

	let {
		scope,
		collectionId
	}: {
		scope: OrganizeCollectionScope;
		collectionId: string;
	} = $props();

	let rules = $state<OrganizeCollectionRuleRef[]>([]);

	$effect(() => {
		const currentScope = scope;
		const id = collectionId;
		let cancelled = false;
		rules = [];
		api
			.getOrganizeCollectionRules(currentScope, id)
			.then((response) => {
				if (cancelled) return;
				rules = response.success && Array.isArray(response.data) ? response.data : [];
			})
			.catch(() => {
				if (!cancelled) rules = [];
			});
		return () => {
			cancelled = true;
		};
	});
</script>

{#if rules.length > 0}
	<a
		class="flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs text-fg-muted hover:bg-surface-2 hover:text-fg"
		href={collectionRulesHref(scope, rules)}
		role="menuitem"
	>
		<Icon name="sparkles" className="h-3.5 w-3.5" />
		{filledByLabel(rules.length)}
	</a>
{/if}
