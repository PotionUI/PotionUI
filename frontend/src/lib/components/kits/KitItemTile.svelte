<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { IconButton, Menu } from '$lib/components/ui';
	import { formatTimecode } from '$lib/media/editors/timecode';
	import { KIT_KIND_ICONS } from '$lib/kits/kitCounts';
	import { checkName, liveName, suggestFree } from '$lib/kits/kitNaming';
	import { formatKitPath } from '$lib/kits/kitPath';
	import type { KitItem } from '$lib/kits/types';
	import KitAudioPlayer from './KitAudioPlayer.svelte';

	let {
		item,
		handle,
		taken,
		position,
		lifted = false,
		onRename,
		onRemove,
		onPreview,
		onEditText,
		onDragStart,
		onDragEnd,
		onNudge
	}: {
		item: KitItem;
		handle: string;
		taken: string[];
		position: number;
		lifted?: boolean;
		onRename: (item: KitItem, name: string) => Promise<{ ok: boolean; clash?: boolean; suggestion?: string | null }>;
		onRemove: (item: KitItem) => void;
		onPreview: (item: KitItem) => void;
		onEditText: (item: KitItem) => void;
		onDragStart: (item: KitItem, event: DragEvent) => void;
		onDragEnd: () => void;
		onNudge: (item: KitItem, step: -1 | 1) => void;
	} = $props();

	let draft = $state('');
	let focused = $state(false);
	let saving = $state(false);
	let failure = $state<string | null>(null);
	let taken409 = $state<string | null>(null);

	let isDefault = $derived(/^(image|video|audio|text)\d+$/.test(item.name));
	let verdict = $derived(checkName(draft, taken.filter((name) => name !== item.name)));
	let meta = $derived(
		item.kind === 'text'
			? ''
			: item.kind === 'image'
				? item.width && item.height
					? `${item.width}×${item.height}`
					: ''
				: item.duration_seconds
					? formatTimecode(item.duration_seconds)
					: ''
	);
	let badge = $derived(
		item.kind === 'video' || item.kind === 'audio'
			? item.duration_seconds
				? formatTimecode(item.duration_seconds)
				: ''
			: ''
	);

	function begin() {
		if (focused) return;
		draft = item.name;
		failure = null;
		taken409 = null;
		focused = true;
	}

	function reset() {
		focused = false;
		failure = null;
		draft = item.name;
	}

	async function commit() {
		if (!focused) return;
		if (draft === item.name) {
			reset();
			return;
		}
		if (!verdict.ok) return;
		saving = true;
		const result = await onRename(item, draft);
		saving = false;
		if (result.ok) focused = false;
		else if (result.clash) {
			taken409 = result.suggestion ?? suggestFree(draft, [...taken, draft]);
			failure = 'Already used.';
		} else failure = 'Could not rename. Try again.';
	}

	function key(event: KeyboardEvent) {
		if (event.key === 'Enter') {
			event.preventDefault();
			void commit();
		} else if (event.key === 'Escape') {
			event.preventDefault();
			reset();
			(event.currentTarget as HTMLElement).blur();
		}
	}

	function tileKey(event: KeyboardEvent) {
		if (!event.altKey || (event.target as HTMLElement).tagName === 'INPUT') return;
		if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
			event.preventDefault();
			onNudge(item, -1);
		} else if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
			event.preventDefault();
			onNudge(item, 1);
		}
	}

	function useSuggestion() {
		if (verdict.suggestion) draft = verdict.suggestion;
	}
</script>

<div
	class="group relative {lifted ? 'z-20 rotate-[-1.5deg] opacity-90' : ''}"
	data-kit-item={item.id}
	data-kit-item-name={item.name}
	role="presentation"
	onkeydown={tileKey}
	ondragend={onDragEnd}
>
	<article
		aria-label="{item.name}, item {position}"
		class="overflow-hidden rounded-lg border bg-surface-1 shadow-raised {lifted
			? 'border-signal shadow-floating'
			: focused
				? 'border-signal/60'
				: 'border-line hover:border-line-strong'}"
	>
		{#if item.missing}
			<div class="flex aspect-square flex-col items-center justify-center gap-2 bg-surface-2 p-3 text-center" data-kit-missing>
				<Icon name="warning" className="h-6 w-6 text-warning" strokeWidth={1.75} />
				<p class="text-sm font-semibold text-fg">File deleted</p>
				<p class="text-sm text-fg-muted">Remove this item or put the file back in the Library.</p>
			</div>
		{:else if item.kind === 'image' || item.kind === 'video'}
			<button
				type="button"
				class="relative block aspect-square w-full overflow-hidden bg-black"
				aria-label="Preview {item.name}"
				onclick={() => onPreview(item)}
			>
				{#if item.thumbnail_url || (item.kind === 'image' && item.url)}
					<img src={item.thumbnail_url ?? item.url} alt="" loading="lazy" class="h-full w-full object-cover" />
				{/if}
				<span class="absolute left-1.5 top-1.5 inline-flex h-6 items-center gap-1 rounded bg-black/70 px-1.5 text-white backdrop-blur-sm">
					<Icon name={KIT_KIND_ICONS[item.kind]} className="h-3 w-3" />
					{#if badge}<span class="font-mono text-sm tabular-nums">{badge}</span>{/if}
				</span>
				{#if item.kind === 'video'}
					<span class="absolute inset-0 flex items-center justify-center">
						<span class="flex h-9 w-9 items-center justify-center rounded-full bg-black/60 text-white backdrop-blur-sm">
							<Icon name="play" className="h-4 w-4" />
						</span>
					</span>
				{/if}
			</button>
		{:else if item.kind === 'audio'}
			<div class="relative flex aspect-square flex-col bg-surface-2 p-3">
				<span class="mb-2 inline-flex items-center gap-1 self-start rounded bg-surface-3 px-1.5 py-0.5 text-fg-muted">
					<Icon name="audio" className="h-3 w-3" />
				</span>
				<div class="min-h-0 flex-1"><KitAudioPlayer url={item.url ?? ''} name={item.name} /></div>
			</div>
		{:else}
			<button
				type="button"
				class="relative flex aspect-square w-full flex-col bg-surface-2 p-3 text-left"
				aria-label="Edit {item.name}"
				onclick={() => onEditText(item)}
			>
				<span class="mb-2 inline-flex items-center gap-1 self-start font-mono text-sm uppercase tracking-[0.07em] text-fg-subtle">
					<Icon name="document" className="h-3 w-3" />Text
				</span>
				<p class="line-clamp-5 text-base leading-relaxed text-fg">{item.text}</p>
				<span class="mt-auto pt-2 font-mono text-sm tabular-nums text-fg-subtle">{(item.text ?? '').length} characters</span>
			</button>
		{/if}

		<span
			class="absolute right-1.5 top-1.5 z-10 flex items-center gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-focus-within:opacity-100 group-hover:opacity-100 {lifted ? '!opacity-100' : ''}"
		>
			<span
				role="presentation"
				draggable="true"
				class="inline-flex"
				ondragstart={(event) => onDragStart(item, event)}
			>
				<IconButton icon="grip" label="Move {item.name}" size="xs" variant="overlay" class="cursor-grab" tabindex={-1} />
			</span>
			<Menu label="{item.name} options" icon="more" size="xs" width={176} align="right">
				{#snippet children({ close })}
					<button
						type="button"
						role="menuitem"
						class="flex w-full items-center gap-2 rounded px-3 py-1.5 text-left text-sm text-fg-muted hover:bg-surface-2 hover:text-fg"
						onclick={() => {
							close();
							onRemove(item);
						}}
					>
						<Icon name="trash" className="h-4 w-4 text-danger" />
						<span class="text-danger">Remove from kit</span>
					</button>
				{/snippet}
			</Menu>
		</span>

		<div class="border-t border-line p-1.5">
			<div class="flex items-center gap-2">
				<input
					class="h-8 min-w-0 flex-1 rounded border px-2 font-mono text-sm transition-colors focus:outline-none {focused
						? `bg-field-bg text-fg shadow-well ${verdict.ok ? 'border-signal' : 'border-danger'}`
						: `border-transparent bg-transparent hover:border-line-strong ${isDefault ? 'text-fg-muted' : 'text-fg'}`}"
					value={focused ? draft : item.name}
					aria-label="Item name"
					aria-invalid={focused && !verdict.ok}
					disabled={saving}
					onfocus={begin}
					oninput={(event) => {
						const input = event.currentTarget as HTMLInputElement;
						draft = liveName(input.value);
						input.value = draft;
						failure = null;
					}}
					onkeydown={key}
					onblur={() => void commit()}
				/>
				{#if meta && !focused}<span class="flex-shrink-0 font-mono text-sm tabular-nums text-fg-subtle">{meta}</span>{/if}
			</div>
			{#if focused}
				<div class="mt-1 space-y-1 px-0.5">
					{#if !verdict.ok && verdict.message}
						<p class="text-sm text-danger" role="alert">
							{verdict.message}
							{#if verdict.suggestion}
								<button type="button" class="font-mono underline" onmousedown={(event) => event.preventDefault()} onclick={useSuggestion}>{verdict.suggestion}</button>
							{/if}
						</p>
					{:else}
						<p class="break-all font-mono text-sm text-fg-subtle">{formatKitPath(handle, draft)}</p>
						<p class="text-sm text-fg-subtle">Spaces become _ and capitals become lowercase.</p>
					{/if}
					{#if failure}
						<p class="text-sm text-danger" role="alert">
							{failure}
							{#if taken409}
								<button type="button" class="font-mono underline" onmousedown={(event) => event.preventDefault()} onclick={() => { draft = taken409 ?? draft; failure = null; taken409 = null; }}>{taken409}</button>
							{/if}
						</p>
					{/if}
				</div>
			{/if}
		</div>
	</article>
</div>
