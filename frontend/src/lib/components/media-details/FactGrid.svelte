<script lang="ts" module>
	export interface Fact {
		label: string;
		value: string;
		mono?: boolean;
	}
</script>

<script lang="ts">
	import ValueBox from '$lib/components/generation/ValueBox.svelte';

	let { facts }: { facts: Fact[] } = $props();

	const spanFor = (value: string) => (value.length > 14 ? 2 : 1);
</script>

<dl class="m-0 grid grid-cols-2 gap-2 px-3" data-fact-grid>
	{#each facts as fact (fact.label)}
		<ValueBox label={fact.label} raw={fact.value} mono={fact.mono} span={spanFor(fact.value)}>
			{fact.value}
		</ValueBox>
	{/each}
</dl>
