<script>
	import { onMount } from 'svelte';

	let { name, props = {}, children } = $props();

	let root;
	let tray = $state();
	let handle;
	let entry;

	function propsWithChildren() {
		return children
			? { ...props, children: (el) => el.append(...tray.childNodes) }
			: props;
	}

	onMount(() => {
		entry = window.__potionui.components[name];
		if (!entry) {
			console.error(`Unknown host component '${name}'`);
			return;
		}
		handle = entry.mount(root, propsWithChildren());
		return () => {
			entry.unmount(handle);
			handle = null;
		};
	});

	$effect(() => {
		const next = props;
		if (handle) entry.update(handle, next);
	});
</script>

<div bind:this={root} style="display: contents"></div>
{#if children}
	<div bind:this={tray} hidden>{@render children()}</div>
{/if}
