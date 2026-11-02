<script lang="ts">
	import { onMount } from 'svelte';
	import type { ToolTabHost } from '$lib/tool-tabs/types';

	let { host }: { host: ToolTabHost } = $props();

	onMount(() => host.setActions({ save: { label: 'Save', run: () => undefined } }));
</script>

<div data-testid="actions-fixture"></div>
