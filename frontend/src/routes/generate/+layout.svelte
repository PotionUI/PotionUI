<script lang="ts">
	import { onMount } from 'svelte';
	import { get } from 'svelte/store';
	import { tabsStore } from '$lib/stores/tabs';
	import { handleBeforeUnload } from '$lib/utils/unsavedChangesGuard';

	function onBeforeUnload(event: BeforeUnloadEvent) {
		handleBeforeUnload(event, get(tabsStore).tabs);
	}

	onMount(() => {
		window.addEventListener('beforeunload', onBeforeUnload);
		return () => window.removeEventListener('beforeunload', onBeforeUnload);
	});
</script>

<slot />
