<script lang="ts">
	import { tick } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { IconButton, Input, Kbd, Menu } from '$lib/components/ui';
	import type { MenuItem } from '$lib/components/ui/Menu.svelte';
	import type { SourceLook } from '$lib/tool-tabs/image/recentEdits';

	let {
		id,
		name,
		unnamed = false,
		look,
		time,
		picture,
		clipUrl = null,
		duration = null,
		missingLabel = 'Picture not found',
		tile,
		onsize,
		openElsewhere = false,
		actionLabel,
		ariaLabel,
		editable = false,
		nameLink = true,
		renameValue = '',
		renamePlaceholder = '',
		onopen,
		onrename,
		onduplicate,
		ondelete
	}: {
		id: string;
		name: string;
		unnamed?: boolean;
		look: SourceLook;
		time: string;
		picture: string | null | undefined;
		clipUrl?: string | null;
		duration?: string | null;
		missingLabel?: string;
		tile?: { width: number; height: number };
		onsize?: (width: number, height: number) => void;
		openElsewhere?: boolean;
		actionLabel: string;
		ariaLabel: string;
		editable?: boolean;
		nameLink?: boolean;
		renameValue?: string;
		renamePlaceholder?: string;
		onopen: () => void;
		onrename?: (draft: string) => Promise<boolean>;
		onduplicate?: () => void;
		ondelete?: () => void;
	} = $props();

	const ITEMS: MenuItem[] = [
		{ id: 'rename', label: 'Rename', icon: 'pencil' },
		{ id: 'duplicate', label: 'Duplicate', icon: 'copy' },
		{ id: 'sep', label: '', separator: true },
		{ id: 'delete', label: 'Delete', icon: 'trash', danger: true }
	];

	let menuOpen = $state(false);
	let renaming = $state(false);
	let saving = $state(false);
	let draft = $state('');
	let failed = $state(false);
	let clipFailed = $state(false);
	let footer = $state<HTMLElement>();

	let shown = $derived(picture && !failed ? picture : null);
	let playable = $derived(!shown && clipUrl && !clipFailed ? clipUrl : null);
	let missing = $derived(!shown && !playable && (picture === null || failed || clipFailed));

	$effect(() => {
		void picture;
		failed = false;
	});

	$effect(() => {
		void clipUrl;
		clipFailed = false;
	});

	function pick(action: string) {
		if (action === 'rename') void beginRename();
		else if (action === 'duplicate') onduplicate?.();
		else if (action === 'delete') ondelete?.();
	}

	async function beginRename() {
		draft = renameValue;
		renaming = true;
		await tick();
		const input = footer?.querySelector('input');
		input?.focus();
		input?.setSelectionRange(input.value.length, input.value.length);
	}

	async function commit() {
		if (!renaming || saving) return;
		saving = true;
		const done = (await onrename?.(draft)) ?? true;
		saving = false;
		if (done) renaming = false;
	}

	function cancel() {
		renaming = false;
	}

	function key(event: KeyboardEvent) {
		if (event.key === 'Enter') {
			event.preventDefault();
			void commit();
		} else if (event.key === 'Escape') {
			event.preventDefault();
			event.stopPropagation();
			cancel();
		}
	}
</script>

<div class="group/tile relative min-w-0 {menuOpen ? 'z-30' : ''}" role="listitem" data-edit-tile={id} data-open-elsewhere={openElsewhere ? '' : undefined}>
	<article
		class="flex h-full flex-col overflow-hidden rounded-lg border bg-surface-1 shadow-raised transition-colors duration-100 {renaming
			? 'border-signal/60'
			: openElsewhere
				? 'border-signal/45'
				: 'border-line group-hover/tile:border-line-strong'}"
	>
		<button
			type="button"
			class="group/media relative block w-full cursor-pointer {tile ? '' : 'aspect-[4/3]'} overflow-hidden bg-surface-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-signal"
			style={tile ? `height: ${tile.height}px` : undefined}
			aria-label={ariaLabel}
			data-edit-open
			onclick={onopen}
		>
			{#if shown}
				<img
					src={shown}
					alt=""
					loading="lazy"
					class="absolute inset-0 h-full w-full object-contain"
					onload={(event) => {
						const img = event.currentTarget as HTMLImageElement;
						onsize?.(img.naturalWidth, img.naturalHeight);
					}}
					onerror={() => (failed = true)}
				/>
			{:else if playable}
				<video
					src="{playable}#t=0.1"
					preload="metadata"
					muted
					playsinline
					tabindex="-1"
					aria-hidden="true"
					class="absolute inset-0 h-full w-full object-contain"
					data-clip-frame
					onloadeddata={(event) => {
						const el = event.currentTarget as HTMLVideoElement;
						onsize?.(el.videoWidth, el.videoHeight);
					}}
					onerror={() => (clipFailed = true)}
				></video>
			{:else if missing}
				<span class="absolute inset-0 flex flex-col items-center justify-center gap-1.5 bg-surface-2 px-3 text-center text-fg-muted" data-media-missing>
					<Icon name="warning" className="h-6 w-6 text-warning" strokeWidth={1.75} />
					<span class="text-sm">{missingLabel}</span>
				</span>
			{/if}
			<span
				class="pointer-events-none absolute inset-0 bg-scrim/0 transition-colors duration-100 group-hover/tile:bg-scrim/35 group-focus-visible/media:bg-scrim/35 max-md:group-hover/tile:bg-scrim/0 [@media(hover:none)]:group-hover/tile:bg-scrim/0"
			></span>
			{#if duration}
				<span
					class="pointer-events-none absolute bottom-1.5 right-1.5 z-[3] inline-flex h-6 items-center rounded bg-scrim/70 px-1.5 font-mono text-sm tabular-nums text-scrim-on backdrop-blur-sm"
					data-clip-duration>{duration}</span
				>
			{/if}
			{#if openElsewhere}
				<span
					class="absolute left-1.5 top-1.5 z-[3] inline-flex h-6 max-w-[calc(100%-48px)] items-center gap-1.5 whitespace-nowrap rounded bg-scrim/70 px-2 text-sm font-medium text-scrim-on backdrop-blur-sm"
					data-open-badge
				>
					<span class="h-1.5 w-1.5 flex-none rounded-full bg-signal"></span>
					<span class="max-md:hidden">Open in another tab</span>
					<span class="md:hidden">In another tab</span>
				</span>
			{/if}
			<span
				style="max-width: calc(100% - 16px)"
				class="pointer-events-none absolute bottom-2.5 left-1/2 z-[2] inline-flex h-7 -translate-x-1/2 items-center gap-1.5 whitespace-nowrap rounded bg-accent px-2.5 text-sm font-medium text-accent-contrast opacity-0 transition-opacity duration-100 group-hover/tile:opacity-100 group-focus-visible/media:opacity-100 max-md:hidden [@media(hover:none)]:hidden"
			>
				{#if openElsewhere}<Icon name="external-link" className="h-3.5 w-3.5" />{/if}
				<span class="truncate">{actionLabel}</span>
			</span>
		</button>

		<div class="flex min-w-0 flex-col gap-0.5 border-t border-line px-3 py-2.5" bind:this={footer}>
			{#if renaming}
				<div data-keeps-escape>
					<Input
						bind:value={draft}
						type="text"
						maxlength={80}
						autocomplete="off"
						spellcheck={false}
						placeholder={renamePlaceholder}
						aria-label="Name for this edit"
						disabled={saving}
						class="!min-h-8 h-8 !px-2 font-medium"
						onkeydown={key}
						onblur={() => void commit()}
					/>
					<p class="mt-1.5 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-sm text-fg-subtle max-md:hidden">
						<Kbd keys="Enter" /><span>save</span><Kbd keys="Esc" /><span>cancel</span>
					</p>
				</div>
			{:else}
				{#if nameLink}
					<button
						type="button"
						class="block w-full truncate rounded-sm text-left text-base outline-none focus-visible:ring-2 focus-visible:ring-signal {unnamed
							? 'font-medium text-fg-muted'
							: 'font-semibold text-fg'}"
						data-edit-name
						onclick={onopen}
					>
						{name}
					</button>
				{:else}
					<span class="block w-full truncate text-left text-base {unnamed ? 'font-medium text-fg-muted' : 'font-semibold text-fg'}" data-edit-name>{name}</span>
				{/if}
				<div class="flex min-w-0 flex-wrap items-center justify-between gap-x-2 gap-y-0.5 text-sm text-fg-subtle">
					<span class="inline-flex min-w-0 items-center gap-1.5 whitespace-nowrap">
						<Icon name={look.icon} className="h-3.5 w-3.5 flex-shrink-0" />{look.label}
					</span>
					<span class="whitespace-nowrap">{time}</span>
				</div>
			{/if}
		</div>
	</article>

	{#if editable}
		<span
			class="absolute right-1.5 top-1.5 z-[5] opacity-0 transition-opacity duration-100 focus-within:opacity-100 group-hover/tile:opacity-100 max-md:opacity-100 [@media(hover:none)]:opacity-100 {menuOpen
				? '!opacity-100'
				: ''}"
		>
			<Menu label="Options for {name}" bind:open={menuOpen} items={ITEMS} onselect={pick} width={176} align="right">
				{#snippet trigger({ open, toggle })}
					<IconButton icon="more" label="Options for {name}" tooltip="More" size="xs" variant="overlay" class="min-h-7 min-w-7" ariaExpanded={open} onclick={toggle} />
				{/snippet}
			</Menu>
		</span>
	{/if}
</div>
