<script lang="ts">
	import { Badge } from '$lib/components/ui';
	import { MODEL_TYPE_UNDEFINED, modelTypePresentation } from '$lib/utils/modelPresentation';

	let { modelType }: { modelType: string } = $props();
</script>

{#if modelType === MODEL_TYPE_UNDEFINED}
	<Badge variant="warning" size="sm">{modelTypePresentation(modelType).label}</Badge>
{:else}
	<Badge variant="neutral" size="sm" class="uppercase">{modelType}</Badge>
{/if}
