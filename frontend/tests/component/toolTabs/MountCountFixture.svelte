<script lang="ts">
	import { onMount } from 'svelte';
	import type { ToolTabHost } from '$lib/tool-tabs/types';

	let { host }: { host: ToolTabHost } = $props();

	onMount(() => {
		const counter = globalThis as unknown as { __mounts?: string[] };
		(counter.__mounts ??= []).push(host.source?.name ?? '');
	});
</script>

<div data-testid="mount-fixture">{host.source?.name}</div>
