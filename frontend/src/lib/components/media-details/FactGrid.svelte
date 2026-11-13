<script lang="ts">
	export interface Fact {
		label: string;
		value: string;
		mono?: boolean;
	}

	let { facts }: { facts: Fact[] } = $props();
</script>

<dl class="m-0 grid grid-cols-2 gap-x-6 gap-y-3 px-3" data-fact-grid>
	{#each facts as fact (fact.label)}
		<div class="min-w-0 {fact.value.length > 22 ? 'col-span-full' : ''}" data-fact>
			<dt class="min-h-5 text-sm text-fg-muted">{fact.label}</dt>
			<dd class="m-0 text-md text-fg [overflow-wrap:anywhere] {fact.mono ? 'font-mono tabular-nums' : ''}">{fact.value}</dd>
		</div>
	{/each}
</dl>
