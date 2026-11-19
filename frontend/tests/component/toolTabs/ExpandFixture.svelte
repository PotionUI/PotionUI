<script lang="ts">
	import { untrack } from 'svelte';
	import ExpandOptions from '$lib/components/tool-tabs/generate/ExpandOptions.svelte';
	import { provideGenerateController } from '$lib/tool-tabs/image/generate/context';
	import type { GenerateController } from '$lib/tool-tabs/image/generate/controller';

	let { controller }: { controller: GenerateController } = $props();
	provideGenerateController(untrack(() => controller));
	const host = { render: async () => Promise.reject(new Error('no picture in tests')) };
</script>

<ExpandOptions host={host as never} phone={false} />
