<script lang="ts">
	import type { Snippet } from 'svelte';
	import { CopyButton } from '$lib/components/ui';
	import { copyFromBox } from './boxCopy';
	import PromptRefText from './PromptRefText.svelte';
	import { hasRefTokens } from './promptRefs';

	let {
		label,
		text,
		raw,
		mono = false,
		axisTag = '',
		formData = null,
		prefix,
		suffix,
		footer
	}: {
		label: string;
		text: string;
		raw: string;
		mono?: boolean;
		axisTag?: string;
		formData?: Record<string, unknown> | null;
		prefix?: Snippet;
		suffix?: Snippet;
		footer?: Snippet;
	} = $props();

	let open = $state(false);
	let head = $state<HTMLElement>();
	let textBox = $state<HTMLElement>();
	let hot = $state(false);
	let overflowing = $state(false);

	function watchOverflow(node: HTMLElement, _text: string) {
		const measure = () => {
			if (open) return;
			overflowing = node.scrollHeight > node.clientHeight + 1;
		};
		measure();
		const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null;
		observer?.observe(node);
		return {
			update: measure,
			destroy: () => observer?.disconnect()
		};
	}
</script>

<section class="px-3 pb-3" data-testid="parameter-block">
	<div bind:this={head} class="mb-1.5 flex items-center gap-1.5 text-sm font-semibold text-fg-muted">
		{@render prefix?.()}
		<span>{label}</span>
		{@render suffix?.()}
		{#if axisTag}
			<span
				data-testid="axis-tag"
				class="rounded border border-signal/40 bg-signal/10 px-1 font-mono text-sm text-signal"
			>
				{axisTag}
			</span>
		{/if}
		<CopyButton text={raw} ariaLabel="Copy {label}" size="xs" class="ml-auto -mr-[5px] {hot ? '!text-fg' : ''}" />
	</div>
	<div
		bind:this={textBox}
		role="presentation"
		class="cursor-pointer rounded border border-line bg-surface-2 px-3 py-2.5 transition-colors hover:border-line-hover active:bg-surface-3"
		onmouseenter={() => (hot = true)}
		onmouseleave={() => (hot = false)}
		onclick={(event) => textBox && head && copyFromBox(event, textBox, head.querySelector<HTMLButtonElement>('button:last-of-type'))}
	>
		<div
			use:watchOverflow={text}
			class="clamp whitespace-pre-wrap text-fg [overflow-wrap:anywhere] {mono
				? 'font-mono text-sm leading-5 tabular-nums'
				: 'text-sm leading-relaxed'}"
			class:open
		>
			{#if hasRefTokens(text)}<PromptRefText {text} {formData} />{:else}{text}{/if}
		</div>
		{#if overflowing || open}
			<button
				type="button"
				class="mt-1.5 text-sm text-signal hover:underline"
				aria-expanded={open}
				onclick={() => (open = !open)}
			>
				{open ? 'Show less' : 'Show more'}
			</button>
		{/if}
	</div>
	{@render footer?.()}
</section>

<style>
	.clamp {
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 10;
		line-clamp: 10;
		overflow: hidden;
	}
	.clamp.open {
		display: block;
		-webkit-line-clamp: unset;
		line-clamp: unset;
		overflow: visible;
	}
</style>
