<script lang="ts">
	import ConditionBuilder from '../../../src/lib/components/organize/ConditionBuilder.svelte';
	import type { RuleDraft } from '../../../src/lib/organize/draft';
	import type { OrganizeCatalog } from '../../../src/lib/types/organize';

	let { initial, catalog }: { initial: RuleDraft; catalog: OrganizeCatalog } = $props();
	let draft = $state<RuleDraft>(initial);
</script>

<ConditionBuilder bind:draft {catalog} />
<pre data-testid="draft-json">{JSON.stringify(draft.conditions.map((c) => ({ fact: c.fact, operator: c.operator, value: c.value })))}</pre>
