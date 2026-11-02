<script lang="ts">
	import type { Snippet } from 'svelte';
	import dismiss from '$lib/actions/dismiss';
	import focusTrap from '$lib/actions/focusTrap';
	import overlayLayer from '$lib/actions/overlayLayer';
	import { getFrameContext } from '$lib/tool-tabs/frame/context';

	let {
		label,
		onclose,
		header,
		children
	}: {
		label: string;
		onclose: () => void;
		header?: Snippet;
		children: Snippet;
	} = $props();

	const frame = getFrameContext();
	const host = frame?.sheetHost ?? null;

	function mountInto(node: HTMLElement) {
		(host ?? document.body).appendChild(node);
		return {
			destroy() {
				node.remove();
			}
		};
	}
</script>

<div
	use:mountInto
	use:overlayLayer
	class="{host ? 'absolute' : 'fixed'} inset-0 flex flex-col justify-end"
	data-frame-sheet
>
	<button
		type="button"
		class="absolute inset-0 cursor-default bg-scrim/60"
		aria-label="Close"
		tabindex="-1"
		onclick={onclose}
	></button>
	<div
		use:dismiss={{ onDismiss: onclose, outside: false }}
		use:focusTrap
		class="relative flex max-h-[82%] flex-col overflow-y-auto rounded-t-xl border-t border-line-strong bg-surface-1 px-2 pb-3 shadow-overlay"
		role="dialog"
		aria-modal="true"
		aria-label={label}
	>
		<div class="mx-auto mb-1.5 mt-2 h-1 w-9 flex-none rounded-sm bg-line-strong"></div>
		{#if header}
			<div class="flex flex-none items-center gap-2 px-2 pb-2.5 pt-1">
				{@render header()}
			</div>
		{/if}
		{@render children()}
	</div>
</div>
