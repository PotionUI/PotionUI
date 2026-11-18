<script lang="ts">
	import portal from '$lib/actions/portal';

	let { onClose }: { onClose: () => void } = $props();
	let scrim = $state(true);
</script>

{#if scrim}
	<div class="drawer-scrim" use:portal role="presentation"></div>
{/if}

<div role="dialog" aria-label="Formulas" use:portal>
	<button type="button" data-stub-apply onclick={onClose}>Apply</button>
</div>
