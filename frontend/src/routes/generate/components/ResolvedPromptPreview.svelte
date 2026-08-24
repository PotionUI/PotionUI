<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';

	export let prompt = '';
	export let negativePrompt = '';

	let open = false;
	let active: 'prompt' | 'negative' = 'prompt';
	let copied = false;

	$: activeText = active === 'prompt' ? prompt : negativePrompt;
	$: wordCount = activeText.trim() ? activeText.trim().split(/\s+/).length : 0;

	async function copyActive() {
		if (!activeText) return;
		await navigator.clipboard.writeText(activeText);
		copied = true;
		setTimeout(() => (copied = false), 1600);
	}
</script>

<div class="overflow-hidden rounded-lg border border-line bg-surface-1">
	<div class="flex items-center">
		<button
			type="button"
			class="flex min-w-0 flex-1 items-center gap-2 px-3 py-2.5 text-left hover:bg-surface-2"
			on:click={() => (open = !open)}
			aria-expanded={open}
		>
			<Icon name="document" className="h-4 w-4 flex-shrink-0 text-fg-subtle" />
			<span class="min-w-0 flex-1">
				<span class="block text-xs font-medium text-fg">Resolved prompt</span>
				<span class="block truncate font-mono text-2xs text-fg-subtle">
					{prompt || 'No enabled prompt content'}
				</span>
			</span>
			<Icon name="chevron-down" className="h-4 w-4 flex-shrink-0 text-fg-subtle transition-transform {open ? 'rotate-180' : ''}" />
		</button>
		{#if open && activeText}
			<Tooltip text={copied ? 'Copied' : 'Copy resolved prompt'} position="top">
				<button type="button" class="mr-2 inline-flex h-8 w-8 items-center justify-center rounded text-fg-muted hover:bg-surface-2 hover:text-fg" on:click={copyActive} aria-label="Copy resolved prompt">
					<Icon name={copied ? 'check' : 'copy'} className="h-4 w-4 {copied ? 'text-success' : ''}" />
				</button>
			</Tooltip>
		{/if}
	</div>

	{#if open}
		<div class="border-t border-line p-3">
			<div class="mb-3 inline-flex rounded bg-surface-2 p-0.5" role="tablist" aria-label="Resolved prompt type">
				<button type="button" role="tab" aria-selected={active === 'prompt'} class="rounded px-2.5 py-1 text-xs {active === 'prompt' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:text-fg'}" on:click={() => (active = 'prompt')}>Prompt</button>
				<button type="button" role="tab" aria-selected={active === 'negative'} class="rounded px-2.5 py-1 text-xs {active === 'negative' ? 'bg-danger/10 text-danger' : 'text-fg-muted hover:text-fg'}" on:click={() => (active = 'negative')}>Negative</button>
			</div>
			<div class="max-h-40 overflow-y-auto whitespace-pre-wrap rounded bg-surface-2 p-3 text-sm leading-relaxed text-fg-muted">
				{activeText || `No ${active === 'negative' ? 'negative ' : ''}prompt content`}
			</div>
			<p class="mt-2 font-mono text-2xs tabular-nums text-fg-subtle">{wordCount} words · {activeText.length} characters</p>
		</div>
	{/if}
</div>
