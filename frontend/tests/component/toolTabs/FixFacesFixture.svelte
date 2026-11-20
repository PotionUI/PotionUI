<script lang="ts">
	import { untrack } from 'svelte';
	import type { Readable } from 'svelte/store';
	import DetectionBoxes from '$lib/components/tool-tabs/generate/DetectionBoxes.svelte';
	import FixFacesOptions from '$lib/components/tool-tabs/generate/FixFacesOptions.svelte';
	import type { EditStackState } from '$lib/tool-tabs/frame/editStack';
	import { provideGenerateController } from '$lib/tool-tabs/image/generate/context';
	import type { GenerateController } from '$lib/tool-tabs/image/generate/controller';

	let {
		controller,
		stack,
		phone = false
	}: {
		controller: GenerateController;
		stack: Readable<EditStackState<unknown>>;
		phone?: boolean;
	} = $props();
	const ctl = untrack(() => controller);
	provideGenerateController(ctl);
	const host = untrack(() => ({ editor: 'image', stack, source: { size: { width: 1000, height: 800 }, ref: null } }) as never);
	const ui = ctl.ui;
	let found = $derived($ui.detections.fix_faces);
</script>

<FixFacesOptions {host} {phone} />
{#if found?.status === 'ready'}
	<div class="relative" data-fixture-canvas>
		<DetectionBoxes items={found.items} hover={found.hover} ontoggle={(id, on) => ctl.tickDetection('fix_faces', id, on)} onhover={(id) => ctl.hoverDetection('fix_faces', id)} />
	</div>
{/if}
