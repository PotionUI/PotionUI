<script lang="ts">
	import { slide } from 'svelte/transition';
	import type { ReasoningBlock } from '$lib/types/chat';
	import Icon from '$lib/components/Icon.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import { formatThinkingElapsed } from '$lib/chat/thinkingTime';

	let {
		blocks = [],
		isStreaming = false
	}: { blocks?: ReasoningBlock[]; isStreaming?: boolean } = $props();

	let userOpen = $state<boolean | null>(null);
	let now = $state(Date.now());
	let bodyEl = $state<HTMLDivElement | null>(null);

	const shown = $derived(blocks.filter((block) => block.kind === 'omitted' || block.text.trim().length > 0));
	const hasText = $derived(shown.some((block) => block.kind !== 'omitted'));
	const live = $derived(isStreaming && blocks.some((block) => block.open));
	const open = $derived(userOpen ?? live);
	const rawOnly = $derived(shown.length > 0 && shown.every((block) => block.kind === 'raw'));
	const kind = $derived(
		hasText ? (new Set(shown.map((block) => block.kind)).size > 1 ? 'mixed' : shown[0].kind) : 'omitted'
	);

	const elapsedMs = $derived.by(() => {
		let known = false;
		let total = 0;
		for (const block of blocks) {
			if (block.open && block.started_at) {
				total += Math.max(0, now - block.started_at);
				known = true;
			} else if (typeof block.duration_ms === 'number') {
				total += block.duration_ms;
				known = true;
			}
		}
		return known ? total : null;
	});

	const label = $derived.by(() => {
		if (live) return rawOnly ? 'Thinking…' : 'Reasoning…';
		const verb = rawOnly ? 'Thought' : 'Reasoned';
		return elapsedMs === null ? verb : `${verb} for ${formatThinkingElapsed(elapsedMs)}`;
	});

	$effect(() => {
		if (!live) return;
		now = Date.now();
		const handle = setInterval(() => (now = Date.now()), 250);
		return () => clearInterval(handle);
	});

	const pinnedGrowth = $derived(
		live && open ? shown.reduce((sum, block) => sum + block.text.length, 0) : -1
	);

	let followTail = true;

	function onBodyScroll() {
		if (!bodyEl) return;
		followTail = bodyEl.scrollHeight - bodyEl.scrollTop - bodyEl.clientHeight < 24;
	}

	$effect(() => {
		if (!bodyEl) {
			followTail = true;
			return;
		}
		if (pinnedGrowth >= 0 && followTail) bodyEl.scrollTop = bodyEl.scrollHeight;
	});

	function toggle() {
		userOpen = !open;
	}

	function blockDuration(block: ReasoningBlock): string | null {
		return typeof block.duration_ms === 'number' ? formatThinkingElapsed(block.duration_ms) : null;
	}
</script>

{#if shown.length > 0 && !hasText}
	<div
		class="mb-3 flex items-center gap-2 text-sm text-fg-subtle"
		data-testid="reasoning-block"
		data-state={live ? 'live' : 'done'}
		data-kind="omitted"
	>
		{#if live}
			<Spinner size="sm" />
		{/if}
		<span data-testid="reasoning-label">{label}</span>
	</div>
{:else if shown.length > 0}
	<div
		class="mb-3 w-[min(100%,500px)] overflow-hidden rounded-lg border border-line bg-surface-1"
		data-testid="reasoning-block"
		data-state={live ? 'live' : 'done'}
		data-kind={kind}
	>
		<button
			type="button"
			class="flex w-full items-center gap-2 px-3 py-2 text-left text-fg-muted hover:bg-surface-2"
			aria-expanded={open}
			data-testid="reasoning-toggle"
			onclick={toggle}
		>
			{#if live}
				<Spinner size="sm" />
			{/if}
			<span class="text-sm font-normal text-fg-muted" data-testid="reasoning-label">{label}</span>
			{#if live && elapsedMs !== null}
				<span class="font-mono text-sm font-normal tabular-nums text-fg-subtle" data-testid="reasoning-elapsed"
					>{formatThinkingElapsed(elapsedMs)}</span
				>
			{/if}
			<span class="ml-auto flex shrink-0 text-fg-subtle transition-transform" class:rotate-180={open}>
				<Icon name="chat-chevron" className="h-3 w-3" strokeWidth={1.8} />
			</span>
		</button>
		{#if open}
			<div
				bind:this={bodyEl}
				onscroll={onBodyScroll}
				class="max-h-60 overflow-y-auto border-t border-line px-3 py-2"
				role="region"
				aria-label="Model reasoning"
				data-testid="reasoning-body"
				transition:slide={{ duration: 150 }}
			>
				{#each shown as block, index}
					<div
						class="m-0"
						class:mt-2={index > 0}
						class:border-t={index > 0}
						class:border-line={index > 0}
						class:pt-2={index > 0}
						data-testid="reasoning-segment"
						data-kind={block.kind}
					>
						{#if block.kind === 'omitted'}
							<p class="m-0 font-mono text-sm text-fg-subtle">
								Reasoning not shown{#if blockDuration(block)}
									<span class="tabular-nums"> · {blockDuration(block)}</span>{/if}
							</p>
						{:else}
							{#if block.kind === 'summary'}
								<span class="mb-1 block font-mono text-sm uppercase tracking-[0.05em] text-fg-subtle"
									>Summary</span
								>
							{/if}
							<p class="m-0 whitespace-pre-wrap break-words text-sm text-fg-muted">{block.text}</p>
						{/if}
					</div>
				{/each}
			</div>
		{/if}
	</div>
{/if}
