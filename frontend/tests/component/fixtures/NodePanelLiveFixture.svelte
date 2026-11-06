<script lang="ts">
	import type { Readable } from 'svelte/store';
	import NodePanel from '$lib/components/elixirs/NodePanel.svelte';

	let { node, onwriterchange, onrename }: { node: Readable<unknown>; onwriterchange: (change: unknown) => void; onrename: (title: string | null) => void } = $props();
</script>

<NodePanel
	node={$node as never}
	client={{} as never}
	label="Describe"
	presets={[]}
	{onrename}
	onsettingschange={() => {}}
	onchoosepreset={() => {}}
	onpickmedia={() => {}}
	onremoveasset={() => {}}
	writer={{ models: [], targets: [], targetLabel: null, problem: null }}
	{onwriterchange}
/>
