<script lang="ts">
	import { untrack } from 'svelte';
	import type { Readable } from 'svelte/store';
	import UpscaleOptions from '$lib/components/tool-tabs/generate/UpscaleOptions.svelte';
	import type { EditStackState } from '$lib/tool-tabs/frame/editStack';
	import { provideGenerateController } from '$lib/tool-tabs/image/generate/context';
	import type { GenerateController } from '$lib/tool-tabs/image/generate/controller';

	let {
		controller,
		stack,
		size,
		phone = false
	}: {
		controller: GenerateController;
		stack: Readable<EditStackState<unknown>>;
		size: { width: number; height: number };
		phone?: boolean;
	} = $props();
	provideGenerateController(untrack(() => controller));
	const host = untrack(() => ({ editor: 'image', stack, source: { size, ref: null } }) as never);
</script>

<UpscaleOptions {host} {phone} />
