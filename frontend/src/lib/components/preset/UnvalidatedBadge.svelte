<script module lang="ts">
	export const UNVALIDATED_EXPLANATION = 'Not yet tested on real weights; results may vary';
</script>

<script lang="ts">
	import Badge from '$lib/components/ui/Badge.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';

	let { id }: { id: string } = $props();
</script>

<Tooltip inline text={UNVALIDATED_EXPLANATION} position="bottom" delay={200} wrapperClass="inline-flex flex-shrink-0 items-center">
	<span data-unvalidated-badge {id}>
		<Badge variant="warning" size="sm">Not validated</Badge>
	</span>
</Tooltip>
<span class="sr-only" aria-hidden="true" id="{id}-explain">{UNVALIDATED_EXPLANATION}</span>
