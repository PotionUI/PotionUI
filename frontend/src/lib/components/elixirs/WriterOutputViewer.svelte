<script lang="ts">
	import { untrack } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, IconButton } from '$lib/components/ui';
	import CopyButton from '$lib/components/ui/CopyButton.svelte';

	interface ViewerEntry {
		text: string;
		text_id: string;
		for_key: string;
		reasoning?: string | null;
		rolled_instruction?: string | null;
		instruction_seed?: number | null;
	}

	type SectionId = 'rolled' | 'thinking' | 'prompt';

	interface Props {
		entries: ViewerEntry[];
		start?: number;
		stepName: string;
		sources?: Record<string, string>;
		pinned?: Record<string, string>;
		readOnly?: boolean;
		onclose: () => void;
		onedit?: (entry: ViewerEntry, text: string) => void;
		onpin?: (entry: ViewerEntry) => void;
		onunpin?: (entry: ViewerEntry) => void;
	}

	let { entries, start = 0, stepName, sources = {}, pinned = {}, readOnly = false, onclose, onedit, onpin, onunpin }: Props = $props();

	let position = $state(untrack(() => start));
	let open = $state<Record<SectionId, boolean>>({ rolled: false, thinking: false, prompt: true });
	let editing = $state(false);
	let draft = $state('');

	let index = $derived(Math.min(Math.max(position, 0), Math.max(entries.length - 1, 0)));
	let entry = $derived(entries[index]);
	let named = $derived(entry ? sources[entry.for_key] : undefined);
	let subtitle = $derived(entries.length > 1 ? `Prompt ${index + 1} of ${entries.length}${named ? ` · ${named}` : ''}` : (named ?? 'Prompt'));
	let kept = $derived(!!entry && Object.prototype.hasOwnProperty.call(pinned, entry.for_key));
	let words = $derived(entry?.reasoning ? entry.reasoning.trim().split(/\s+/).filter(Boolean).length : 0);

	function go(step: number) {
		const next = index + step;
		if (next < 0 || next >= entries.length) return;
		position = next;
		editing = false;
	}

	function toggle(section: SectionId) {
		open = { ...open, [section]: !open[section] };
	}

	function startEdit() {
		draft = entry.text;
		editing = true;
	}

	function save() {
		const text = draft.trim();
		if (text && (text !== entry.text || !kept)) onedit?.(entry, text);
		editing = false;
	}

	function onkey(event: KeyboardEvent) {
		const target = event.target as HTMLElement | null;
		if (target && (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT')) return;
		if (event.key === 'ArrowLeft') {
			event.preventDefault();
			go(-1);
		} else if (event.key === 'ArrowRight') {
			event.preventDefault();
			go(1);
		}
	}
</script>

<svelte:window onkeydowncapture={onkey} />

{#snippet section(id: SectionId, title: string, meta: string, copy: string, mono: boolean)}
	<section class="block" data-section={id}>
		<div class="head">
			<button type="button" class="toggle" aria-expanded={open[id]} aria-controls="writer-viewer-{id}" onclick={() => toggle(id)}>
				<Icon name={open[id] ? 'chevron-down' : 'chevron-right'} className="h-4 w-4 text-fg-subtle" />
				<span class="title">{title}</span>
				{#if meta}<span class="meta" class:font-mono={mono} class:tabular-nums={mono}>{meta}</span>{/if}
			</button>
			<CopyButton text={copy} label="Copy" size="xs" />
		</div>
		{#if open[id]}
			<div id="writer-viewer-{id}" class="content">
				{#if id === 'prompt' && editing}
					<textarea
						class="input min-h-[10rem] w-full resize-y text-sm"
						aria-label="Edit prompt"
						maxlength="8000"
						bind:value={draft}
						onkeydown={(event) => {
							if (event.key === 'Escape') {
								event.stopPropagation();
								editing = false;
							}
							if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) save();
						}}
					></textarea>
					<div class="actions">
						<Button variant="ghost" size="sm" onclick={() => (editing = false)}>Cancel</Button>
						<Button variant="primary" size="sm" icon="pin" disabled={!draft.trim()} onclick={save}>Save and pin</Button>
					</div>
				{:else}
					<p class="prose" class:prompt={id === 'prompt'} class:muted={id !== 'prompt'}>{copy}</p>
					{#if id === 'prompt' && !readOnly}
						<div class="actions">
							<Button variant="secondary" size="sm" icon="pencil" onclick={startEdit}>Edit</Button>
							<Button variant="secondary" size="sm" icon="pin" pressed={kept} onclick={() => (kept ? onunpin?.(entry) : onpin?.(entry))}>{kept ? 'Unpin' : 'Pin'}</Button>
						</div>
					{/if}
				{/if}
			</div>
		{/if}
	</section>
{/snippet}

<BaseModal isOpen title={stepName} {subtitle} sizeClass="md:w-[min(760px,calc(100vw-64px))] md:max-w-none md:h-[min(86vh,820px)]" on:close={onclose}>
	<svelte:fragment slot="header">
		{#if entries.length > 1}
			<div class="nav">
				<IconButton icon="chevron-left" label="Previous prompt" size="sm" disabled={index === 0} onclick={() => go(-1)} />
				<IconButton icon="chevron-right" label="Next prompt" size="sm" disabled={index >= entries.length - 1} onclick={() => go(1)} />
			</div>
		{/if}
	</svelte:fragment>
	{#if entry}
		<div class="sheet" data-testid="writer-output-viewer">
			{#if entry.rolled_instruction}
				{@render section('rolled', 'Rolled instruction', entry.instruction_seed != null ? `seed ${entry.instruction_seed}` : '', entry.rolled_instruction, true)}
			{/if}
			{#if entry.reasoning}
				{@render section('thinking', 'Thinking', `${words} ${words === 1 ? 'word' : 'words'}`, entry.reasoning, false)}
			{/if}
			{@render section('prompt', 'Prompt', '', entry.text, false)}
		</div>
	{/if}
</BaseModal>

<style>
	.nav {
		display: flex;
		align-items: center;
		gap: 2px;
	}
	.sheet {
		display: flex;
		flex-direction: column;
		gap: 12px;
		padding: 16px;
	}
	.block {
		border: 1px solid rgb(var(--line));
		border-radius: 6px;
		background: rgb(var(--surface-2) / 0.5);
	}
	.head {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 4px 8px 4px 4px;
	}
	.toggle {
		display: flex;
		min-width: 0;
		flex: 1;
		align-items: center;
		gap: 8px;
		min-height: 36px;
		padding: 0 8px;
		border-radius: 4px;
		text-align: left;
	}
	.title {
		font-size: 14px;
		font-weight: 600;
		color: rgb(var(--fg));
	}
	.meta {
		margin-left: auto;
		font-size: 12px;
		color: rgb(var(--fg-subtle));
	}
	.content {
		display: flex;
		flex-direction: column;
		gap: 12px;
		padding: 4px 16px 16px;
	}
	.prose {
		margin: 0;
		font-size: 14px;
		line-height: 1.65;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		color: rgb(var(--fg));
	}
	.prose.prompt {
		font-size: 15px;
	}
	.prose.muted {
		color: rgb(var(--fg-muted));
	}
	.actions {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
	}
	:global(:where(html:not([data-input='pointer']))) .toggle:focus-visible {
		outline: 2px solid rgb(var(--signal));
		outline-offset: 1px;
	}
</style>
