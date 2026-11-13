<script lang="ts">
	import type { Snippet } from 'svelte';
	import { CopyButton } from '$lib/components/ui';

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
</script>

<div
	class="relative flex min-w-0 flex-col-reverse justify-end rounded border border-line bg-surface-2 px-2.5 py-2"
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
			class="absolute right-1 top-1 !text-fg-subtle hover:!text-fg"
		/>
	</dd>
</div>
