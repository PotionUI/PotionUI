<script lang="ts">
	import type { Snippet } from 'svelte';

	let { titleExtra, afterMain }: { titleExtra?: Snippet; afterMain?: Snippet } = $props();
</script>

<header data-editor-title>
	{#if titleExtra}{@render titleExtra()}{/if}
</header>
<div data-editor-after>
	{#if afterMain}{@render afterMain()}{/if}
</div>
