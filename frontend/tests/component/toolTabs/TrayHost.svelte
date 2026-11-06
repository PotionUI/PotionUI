<script lang="ts">
	import LookTray, { type TrayItem } from '$lib/components/tool-tabs/frame/LookTray.svelte';

	let {
		items,
		original = null,
		phone = false,
		log
	}: {
		items: TrayItem[];
		original?: { selected: boolean } | null;
		phone?: boolean;
		log: (entry: string) => void;
	} = $props();
</script>

<LookTray {items} {original} {phone} onpick={(id) => log(`pick:${id}`)} onpreview={(id) => log(`preview:${id}`)}>
	{#snippet thumb(item)}<span data-thumb={item.id}></span>{/snippet}
</LookTray>
