<script lang="ts">
	import { onDestroy, tick } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { processMarkdown } from '$lib/utils/markdown';

	let {
		title,
		value = '',
		editable = true,
		saving = false,
		renderMode = 'plain',
		collapsedHeight = 240,
		textareaHeight = 220,
		placeholder = '',
		help = null,
		emptyText = '',
		editableEmptyText = emptyText,
		onSave
	}: {
		title: string;
		value?: string | null | undefined;
		editable?: boolean;
		saving?: boolean;
		renderMode?: 'markdown' | 'plain';
		collapsedHeight?: number;
		textareaHeight?: number;
		placeholder?: string;
		help?: string | null;
		emptyText?: string;
		/** Empty-state copy shown only while `editable`; falls back to `emptyText`. */
		editableEmptyText?: string;
		onSave?: (value: string) => void;
	} = $props();

	let isEditing = $state(false);
	let draft = $state('');

	let contentEl: HTMLDivElement | null = null;
	let expanded = $state(false);
	let overflows = $state(false);
	let observer: ResizeObserver | null = null;

	function measure() {
		if (!contentEl) return;
		// scrollHeight is the full content height whether or not we are clamping it.
		overflows = contentEl.scrollHeight > collapsedHeight + 8;
	}

	// Re-measure when the text changes, and once more after the DOM settles: markdown
	// can reflow as fonts load, and content that fits at first paint may not later.
	$effect(() => {
		void value;
		expanded = false;
		tick().then(measure);
	});

	function attach(node: HTMLDivElement) {
		contentEl = node;
		measure();
		if (typeof ResizeObserver !== 'undefined') {
			observer = new ResizeObserver(measure);
			observer.observe(node);
		}
		return {
			destroy() {
				observer?.disconnect();
				observer = null;
				contentEl = null;
			}
		};
	}

	onDestroy(() => observer?.disconnect());

	function startEdit() {
		draft = value || '';
		isEditing = true;
	}

	function cancelEdit() {
		draft = value || '';
		isEditing = false;
	}

	function save() {
		onSave?.(draft);
		isEditing = false;
	}
</script>

<div>
	<div class="flex items-baseline gap-3 mb-3">
		<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted whitespace-nowrap">
			{title}
		</span>
		<div class="flex-1 h-px bg-line self-center"></div>
		{#if editable}
			<button
				class="text-fg-subtle hover:text-fg-muted p-1 -m-1"
				onclick={() => (isEditing ? cancelEdit() : startEdit())}
				aria-label={isEditing ? 'Cancel editing' : `Edit ${title.toLowerCase()}`}
			>
				<Icon name={isEditing ? 'close' : 'edit'} className="w-4 h-4" />
			</button>
		{/if}
	</div>

	<div class="bg-surface-1 border border-line rounded-lg shadow-raised p-4">
		{#if editable && isEditing}
			<div class="space-y-2">
				<textarea
					bind:value={draft}
					{placeholder}
					class="input font-mono text-sm"
					style="min-height: {textareaHeight}px"
				></textarea>
				{#if help}
					<p class="text-2xs text-fg-subtle">{help}</p>
				{/if}
				<div class="flex gap-2 justify-end">
					<button
						class="px-3 py-1.5 text-sm text-fg-muted hover:bg-surface-3 rounded transition-colors"
						onclick={cancelEdit}
					>
						Cancel
					</button>
					<button
						class="px-3 py-1.5 text-sm bg-accent text-accent-contrast rounded hover:bg-accent-hover transition-colors disabled:opacity-50"
						onclick={save}
						disabled={saving}
					>
						{saving ? 'Saving...' : 'Save'}
					</button>
				</div>
			</div>
		{:else if value}
			<div class="relative">
				{#if renderMode === 'markdown'}
					<div
						use:attach
						class="text-sm text-fg leading-relaxed break-words overflow-hidden"
						style={expanded ? '' : `max-height: ${collapsedHeight}px`}
					>
						{@html processMarkdown(value)}
					</div>
				{:else}
					<div
						use:attach
						class="text-sm text-fg leading-relaxed whitespace-pre-wrap break-words overflow-hidden"
						style={expanded ? '' : `max-height: ${collapsedHeight}px`}
					>
						{value}
					</div>
				{/if}

				{#if overflows && !expanded}
					<!-- Fades the clipped edge so it reads as "more below", not "cut off". -->
					<div
						class="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-surface-1 to-transparent"
					></div>
				{/if}
			</div>

			{#if overflows}
				<button
					type="button"
					class="mt-2 inline-flex items-center gap-1 text-xs text-signal hover:underline"
					onclick={() => (expanded = !expanded)}
					aria-expanded={expanded}
				>
					<Icon name={expanded ? 'chevron-up' : 'chevron-down'} className="w-3.5 h-3.5" />
					{expanded ? 'Show less' : 'Show more'}
				</button>
			{/if}
		{:else}
			<p class="text-sm text-fg-subtle italic">
				{editable ? editableEmptyText : emptyText}
			</p>
		{/if}
	</div>
</div>
