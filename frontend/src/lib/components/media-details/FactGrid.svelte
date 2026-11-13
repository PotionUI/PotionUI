<script lang="ts" module>
	export interface Fact {
		label: string;
		value: string;
		mono?: boolean;
	}
</script>

<script lang="ts">
	let { facts }: { facts: Fact[] } = $props();

	const spanFor = (value: string) => (value.length > 28 ? 3 : value.length > 14 ? 2 : 1);
</script>

<dl class="m-0 grid grid-cols-3 gap-x-4 gap-y-4 px-3" data-fact-grid>
	{#each facts as fact (fact.label)}
		<div
			class="flex min-w-0 flex-col-reverse justify-end"
			style="grid-column: span {spanFor(fact.value)} / span {spanFor(fact.value)}"
			data-fact
		>
			<dt class="mt-0.5 min-h-5 text-sm text-fg-muted">{fact.label}</dt>
			<dd
				class="m-0 text-[15px] font-semibold leading-5 text-fg [overflow-wrap:anywhere] {fact.mono
					? 'font-mono tabular-nums'
					: ''}"
			>
				{fact.value}
			</dd>
		</div>
	{/each}
</dl>
