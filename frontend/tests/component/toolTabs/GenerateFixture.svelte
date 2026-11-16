<script lang="ts">
	import { untrack } from 'svelte';
	import FloatingPrompt from '$lib/components/tool-tabs/generate/FloatingPrompt.svelte';
	import { provideGenerateController } from '$lib/tool-tabs/image/generate/context';
	import type { GenerateController } from '$lib/tool-tabs/image/generate/controller';

	let { controller, ...rest }: { controller: GenerateController; settings?: { open: boolean; toggle: () => void }; running?: string | null; large?: boolean; phone?: boolean } = $props();
	provideGenerateController(untrack(() => controller));
</script>

<FloatingPrompt {...rest} />
