<script lang="ts">
	import type { Snippet } from 'svelte';
	import { CopyButton } from '$lib/components/ui';
	import { copyFromBox } from './boxCopy';

	let {
		label,
		raw,
		mono = false,
		span = 1,
		labelExtra,
		children
	}: {
		label: string;
		raw: string;
		mono?: boolean;
		span?: number;
		labelExtra?: Snippet;
		children?: Snippet;
	} = $props();

	let box = $state<HTMLElement>();
</script>

<div
	bind:this={box}
	role="presentation"
	class="group relative flex min-w-0 cursor-pointer flex-col-reverse justify-end rounded border border-line bg-surface-2 px-2.5 py-2 transition-colors hover:border-line-hover active:bg-surface-3"
	onclick={(event) => box && copyFromBox(event, box, box.querySelector<HTMLButtonElement>('button'))}
	style="grid-column: span {span} / span {span}"
	data-param-cell
>
	<dt class="mt-0.5 flex min-w-0 items-center text-sm text-fg-muted">
		<span class="truncate">{label}</span>
		{@render labelExtra?.()}
	</dt>
	<dd
		class="m-0 min-w-0 pr-6 text-[15px] font-semibold leading-5 text-fg [overflow-wrap:anywhere] {mono
			? 'font-mono tabular-nums'
			: ''}"
	>
		{@render children?.()}
		<CopyButton
			text={raw}
			ariaLabel="Copy {label}"
			size="xs"
			class="absolute right-1 top-1 !text-fg-subtle hover:!text-fg group-hover:!text-fg"
		/>
	</dd>
</div>
