<script lang="ts">
	let { host, phone }: { host: any; phone: boolean } = $props();
	const draft = { tool: 'acme.halftone', icon: 'extension', label: 'Halftone', summary: 'Factor 2', params: { factor: 2 }, lane: 'paint' };
</script>

<div data-plugin-options data-phone={phone}>
	<button type="button" data-plugin-begin onclick={() => host.begin(draft)}>Begin</button>
	<button type="button" data-plugin-label onclick={() => host.setApply({ label: 'Make halftone', enabled: true })}>Label</button>
	<button type="button" data-plugin-apply onclick={() => host.apply()}>Apply it</button>
</div>
