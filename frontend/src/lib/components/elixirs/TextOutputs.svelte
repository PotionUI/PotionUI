<script lang="ts">
	import { Button } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import NodeIconButton from './NodeIconButton.svelte';
	import { tileChoice, type Choice } from '$lib/elixirs/choice';
	import type { OutputEntry } from '$lib/elixirs/types';
	import { tileKey } from '$lib/elixirs/tiles';

	interface TextEntry extends OutputEntry {
		text: string;
		text_id: string;
		for_key: string;
		reasoning?: string | null;
		rolled_instruction?: string | null;
		instruction_seed?: number | null;
	}

	interface Props {
		entries?: TextEntry[];
		pending?: number;
		failed?: number;
		dim?: boolean;
		pinned?: Record<string, string>;
		sources?: Record<string, string>;
		choice?: Choice | null;
		readOnly?: boolean;
		phone?: boolean;
		label: string;
		ontick?: (key: string) => void;
		onedit?: (entry: TextEntry, text: string) => void;
		onpin?: (entry: TextEntry) => void;
		onunpin?: (entry: TextEntry) => void;
	}

	let { entries = [], pending = 0, failed = 0, dim = false, pinned = {}, sources = {}, choice = null, readOnly = false, phone = false, label, ontick, onedit, onpin, onunpin }: Props = $props();

	let editing = $state<string | null>(null);
	let thinking = $state<Record<string, boolean>>({});
	let rolled = $state<Record<string, boolean>>({});
	let draft = $state('');
	let skeletons = $derived(Array.from({ length: Math.max(0, pending) }, (_, index) => index));
	let broken = $derived(Array.from({ length: Math.max(0, failed) }, (_, index) => index));

	function heading(entry: TextEntry, index: number) {
		return sources[entry.for_key] ?? (entries.length > 1 ? `Prompt ${index + 1}` : 'Prompt');
	}

	function isPinned(entry: TextEntry) {
		return Object.prototype.hasOwnProperty.call(pinned, entry.for_key);
	}

	function startEdit(entry: TextEntry) {
		editing = entry.text_id;
		draft = entry.text;
	}

	function save(entry: TextEntry) {
		const text = draft.trim();
		editing = null;
		if (text && (text !== entry.text || !isPinned(entry))) onedit?.(entry, text);
	}
</script>

{#if entries.length > 0 || skeletons.length > 0 || broken.length > 0}
	<ol class="texts" class:dim aria-label={label}>
		{#each entries as entry, index (`${tileKey(entry)}:${index}`)}
			{@const decor = tileChoice(choice, tileKey(entry))}
			{@const kept = isPinned(entry)}
			<li class="row" class:chosen={decor.tick?.checked}>
				{#if decor.tick}
					<button
						type="button"
						role="checkbox"
						class="tick flex h-6 w-6 shrink-0 items-center justify-center rounded"
						aria-checked={decor.tick.checked}
						aria-disabled={decor.tick.locked}
						aria-label="Send {heading(entry, index)} to {decor.tick.label}"
						onclick={() => !decor.tick?.locked && ontick?.(tileKey(entry))}
					>
						<span class="flex h-4 w-4 items-center justify-center rounded {decor.tick.checked ? 'bg-signal-solid text-scrim-on' : 'border border-line-strong text-transparent'}">
							{#if decor.tick.checked}
								<Icon name="check" className="h-3 w-3" />
							{/if}
						</span>
					</button>
				{/if}
				<span class="file truncate font-mono">{heading(entry, index)}</span>
				<div class="body">
					{#if editing === entry.text_id}
						<textarea
							class="input min-h-[7rem] w-full resize-y text-sm"
							aria-label="Edit {heading(entry, index)}"
							maxlength="8000"
							bind:value={draft}
							onkeydown={(event) => {
								if (event.key === 'Escape') {
									event.stopPropagation();
									editing = null;
								}
								if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) save(entry);
							}}
						></textarea>
						<div class="flex justify-end gap-2">
							<Button variant="ghost" size="sm" onclick={() => (editing = null)}>Cancel</Button>
							<Button variant="primary" size="sm" icon="pin" disabled={!draft.trim()} onclick={() => save(entry)}>Save and pin</Button>
						</div>
					{:else}
						<p class="text">{entry.text}</p>
						{#if entry.rolled_instruction}
							{@const open = !!rolled[entry.text_id]}
							<button type="button" class="think" aria-expanded={open} onclick={() => (rolled = { ...rolled, [entry.text_id]: !open })}>
								Rolled instruction
								<Icon name={open ? 'chevron-up' : 'chevron-down'} className="h-3 w-3" />
							</button>
							{#if open}
								<p class="reasoning" tabindex="0" aria-label="Rolled instruction for {heading(entry, index)}">{entry.rolled_instruction}</p>
							{/if}
						{/if}
						{#if entry.reasoning}
							{@const shown = !!thinking[entry.text_id]}
							<button type="button" class="think" aria-expanded={shown} onclick={() => (thinking = { ...thinking, [entry.text_id]: !shown })}>
								Thinking
								<Icon name={shown ? 'chevron-up' : 'chevron-down'} className="h-3 w-3" />
							</button>
							{#if shown}
								<p class="reasoning" tabindex="0" aria-label="Reasoning for {heading(entry, index)}">{entry.reasoning}</p>
							{/if}
						{/if}
					{/if}
				</div>
				{#if editing !== entry.text_id && !readOnly}
					<NodeIconButton icon="pencil" large={phone} label="Edit {heading(entry, index)}" tip="Edit and pin this prompt" onclick={() => startEdit(entry)} />
					<NodeIconButton
						icon="pin"
						large={phone}
						label={kept ? `Unpin ${heading(entry, index)}` : `Pin ${heading(entry, index)}`}
						tip={kept ? 'Pinned: used again next brew' : 'Pin: reuse next brew'}
						pressed={kept}
						onclick={() => (kept ? onunpin?.(entry) : onpin?.(entry))}
					/>
				{/if}
			</li>
		{/each}
		{#each skeletons as index (index)}
			<li class="row placeholder" aria-hidden="true">
				<span class="file font-mono">…</span>
				<div class="body"><p class="text">Writing…</p></div>
			</li>
		{/each}
		{#each broken as index (index)}
			<li class="row bad">
				<span class="file"><Icon name="warning" className="h-4 w-4" /></span>
				<div class="body"><p class="text">Not written</p></div>
			</li>
		{/each}
	</ol>
{/if}

<style>
	.texts {
		margin: 0 16px 16px;
		border: 1px solid rgb(var(--line-strong));
		border-radius: 4px;
		background: rgb(var(--surface-2) / 0.5);
	}
	.texts.dim {
		opacity: 0.55;
	}
	.row {
		display: flex;
		align-items: flex-start;
		gap: 8px;
		padding: 12px 6px 10px 12px;
	}
	.row + .row {
		border-top: 1px solid rgb(var(--line));
	}
	.row.chosen {
		box-shadow: inset 2px 0 0 rgb(var(--signal));
	}
	.file {
		flex: none;
		width: 44px;
		font-size: 12px;
		line-height: 16px;
		color: rgb(var(--fg-subtle));
	}
	.body {
		display: flex;
		min-width: 0;
		flex: 1;
		flex-direction: column;
		gap: 6px;
	}
	.text {
		display: -webkit-box;
		-webkit-line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
		font-size: 12px;
		line-height: 16px;
		color: rgb(var(--fg));
		white-space: pre-line;
	}
	.placeholder {
		animation: pulse 1.6s ease-in-out infinite;
	}
	.placeholder .text,
	.bad .text {
		color: rgb(var(--fg-subtle));
	}
	.bad .file {
		color: rgb(var(--danger));
	}
	.think {
		align-self: flex-start;
		display: inline-flex;
		align-items: center;
		gap: 4px;
		height: 24px;
		margin-left: -4px;
		padding: 0 4px;
		border-radius: 4px;
		font-size: 12px;
		color: rgb(var(--fg-subtle));
	}
	.think:hover {
		color: rgb(var(--fg));
	}
	:global(:where(html:not([data-input='pointer']))) .think:focus-visible,
	:global(:where(html:not([data-input='pointer']))) .tick:focus-visible,
	:global(:where(html:not([data-input='pointer']))) .reasoning:focus-visible {
		outline: 2px solid rgb(var(--signal));
		outline-offset: 1px;
	}
	.reasoning {
		max-height: 84px;
		overflow: auto;
		white-space: pre-line;
		font-size: 12px;
		line-height: 16px;
		color: rgb(var(--fg-muted));
		background: rgb(var(--canvas));
		border: 1px solid rgb(var(--line));
		border-radius: 4px;
		padding: 6px 8px;
		scrollbar-width: thin;
	}
	.tick[aria-disabled='true'] {
		cursor: default;
	}
	@keyframes pulse {
		50% {
			opacity: 0.35;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.placeholder {
			animation: none;
		}
	}
</style>
