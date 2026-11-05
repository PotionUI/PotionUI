<script lang="ts">
	import { untrack, type Component } from 'svelte';
	import { provideOrganizeScope } from '$lib/organize/scope';
	import type { OrganizeScope } from '$lib/types/organize';

	let {
		component: Inner,
		scope = 'instance',
		props = {}
	}: {
		component: Component<any>;
		scope?: OrganizeScope;
		props?: Record<string, unknown>;
	} = $props();

	provideOrganizeScope(untrack(() => scope));
</script>

<Inner {...props} />
