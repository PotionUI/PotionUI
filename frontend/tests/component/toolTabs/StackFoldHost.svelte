<script lang="ts">
	import EditStack from '$lib/components/tool-tabs/frame/EditStack.svelte';
	import { IconButton } from '$lib/components/ui';
	import type { EditStackState, EditStep } from '$lib/tool-tabs/frame/editStack';
	import type { StackProgress, StackVariant } from '$lib/tool-tabs/frame/types';

	let {
		stack,
		isLayer,
		layerThumb,
		variant = 'panel',
		collapsed = false,
		progress = null
	}: {
		stack: EditStackState<unknown>;
		isLayer: (step: EditStep<unknown>) => boolean;
		layerThumb: (step: EditStep<unknown>) => string | null;
		variant?: StackVariant;
		collapsed?: boolean;
		progress?: StackProgress | null;
	} = $props();

	let pressed = $state(0);
	const none = () => undefined;
</script>

<EditStack {stack} original={{ summary: '1024×1024' }} {variant} layered {isLayer} {layerThumb} {progress} bind:collapsed onreopen={none} onseek={none} ontogglehidden={none} onremove={none} onmove={none}>
	{#snippet headerActions()}
		<IconButton icon="plus" label="Add a layer" size="xs" onclick={() => (pressed += 1)} data-header-add data-pressed={pressed || undefined} />
	{/snippet}
</EditStack>
