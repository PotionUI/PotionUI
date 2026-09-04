<script lang="ts">
	// Vision-image attach surface opened from ChatInput's "Add" button: current-
	// form thumbnails, the shared MediaLoaderField (upload/library/paste/history
	// — whatever it already offers, nothing invented here), reuse-last-image,
	// and the auto-attach-next-message checkbox. Portaled + fixed-positioned by
	// its caller (ChatInput computes `style` from the trigger's rect), with its
	// own outside-pointerdown/Escape teardown — the fixed idiom for an anchored
	// popover documented on ChatInput's Tools dropdown (never a portaled
	// full-page catcher next to a non-portaled menu, which desyncs on close).
	import { onMount } from 'svelte';
	import MediaLoaderField from '$lib/components/form-fields/MediaLoaderField.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import IconButton from '$lib/components/ui/IconButton.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import portal from '$lib/actions/portal';
	import type { FormImageEntry } from '$lib/chat/formMedia';

	export let style: string = '';
	export let triggerEl: HTMLElement | undefined = undefined;
	export let onClose: () => void;

	export let formImageEntries: FormImageEntry[] = [];
	export let selectedDurablePath: string | null = null;
	export let onSelectFormImage: (entry: FormImageEntry) => void = () => {};
	export let selectedImageData: unknown = null;
	export let onMediaLoaderChange: (value: unknown) => void = () => {};
	export let lastGeneratedImage: { url: string; name: string; generatedAt?: string } | null = null;
	export let onAttachLastImage: () => void = () => {};
	export let alwaysAttachLastImage = false;
	export let onToggleAttachImage: (() => void) | undefined = undefined;

	let rootEl: HTMLDivElement;

	function isSelected(entry: FormImageEntry): boolean {
		return (entry.media.relative_path || entry.media.path) === selectedDurablePath;
	}

	function pickFormImage(entry: FormImageEntry) {
		onSelectFormImage(entry);
		onClose();
	}

	function attachAgain() {
		onAttachLastImage();
		onClose();
	}

	function handleFieldChange(_fieldName: string, value: unknown) {
		onMediaLoaderChange(value);
	}

	function handleOutsidePointerDown(e: PointerEvent) {
		const target = e.target as Node;
		if (rootEl?.contains(target) || triggerEl?.contains(target)) return;
		// MediaLoaderField opens its own portaled modals (history/library/
		// preview, all BaseModal.svelte) OUTSIDE this popover's DOM subtree even
		// though they're mounted as Svelte descendants of it - a click inside one
		// would otherwise register as "outside" and tear this popover (and the
		// modal with it) down mid-pick. GlobalChatPanel's own shell also carries
		// aria-modal="true" and is an ancestor of every click target inside the
		// open chat panel, so it must be excluded here or this guard would
		// swallow every outside click and this popover would never close.
		const nestedModal = target instanceof Element ? target.closest('[aria-modal="true"]') : null;
		if (nestedModal && nestedModal.getAttribute('aria-label') !== 'AI chat') return;
		onClose();
	}

	function handleOutsideKeydown(e: KeyboardEvent) {
		if (e.key !== 'Escape') return;
		// A nested BaseModal (history/library/preview) owns this Escape itself,
		// via its own capture-phase window listener that runs first - by the
		// time it bubbles here focus is still inside the modal's dialog, so
		// don't also tear down this popover underneath it. GlobalChatPanel's
		// own shell also carries aria-modal="true" and is an ancestor of
		// EVERYTHING in the open chat panel (including this popover's trigger),
		// so it must be excluded here or this guard would swallow every Escape
		// press and this popover would never close on its own.
		const nestedModal = e.target instanceof Element ? e.target.closest('[aria-modal="true"]') : null;
		if (nestedModal && nestedModal.getAttribute('aria-label') !== 'AI chat') return;
		onClose();
		// Keeps GlobalChatPanel's <svelte:window on:keydown> from also closing
		// the whole chat panel on this same Escape press (see ChatInput's Tools
		// dropdown for the same reasoning).
		e.stopPropagation();
	}

	onMount(() => {
		document.addEventListener('pointerdown', handleOutsidePointerDown, true);
		document.addEventListener('keydown', handleOutsideKeydown);
		return () => {
			document.removeEventListener('pointerdown', handleOutsidePointerDown, true);
			document.removeEventListener('keydown', handleOutsideKeydown);
		};
	});
</script>

<div
	use:portal
	bind:this={rootEl}
	class="fixed z-[9999] w-[min(420px,calc(100vw-2rem))] max-h-[min(32rem,calc(100vh-8rem))] overflow-y-auto bg-surface-1 border border-line rounded-xl shadow-floating"
	{style}
	role="dialog"
	aria-label="Attach an image"
	data-testid="chat-attach-popover"
>
	<div class="flex items-start gap-2.5 px-3 py-2.5 border-b border-line">
		<span class="w-7 h-7 rounded-lg bg-signal/10 text-signal grid place-items-center flex-shrink-0">
			<Icon name="image" className="w-3.5 h-3.5" />
		</span>
		<div class="flex-1 min-w-0">
			<div class="text-xs font-semibold text-fg">Attach an image</div>
			<div class="text-2xs text-fg-subtle mt-0.5">One image can be sent with the next message</div>
		</div>
		<span class="font-mono text-2xs px-1.5 py-0.5 rounded bg-signal/10 text-signal tracking-[0.06em] flex-shrink-0">VISION</span>
		<Tooltip text="Close" position="left" delay={150}>
			<IconButton icon="close" label="Close image picker" size="sm" onclick={onClose} />
		</Tooltip>
	</div>

	<div class="p-3 space-y-3">
		{#if formImageEntries.length > 0}
			<div>
				<div class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-1.5">From current form</div>
				<div class="flex gap-1.5 overflow-x-auto pb-0.5">
					{#each formImageEntries as entry (entry.key)}
						<button
							type="button"
							class="relative flex-shrink-0 w-12 h-12 rounded-lg overflow-hidden border transition-colors duration-100 {isSelected(entry)
								? 'border-signal ring-2 ring-signal/40'
								: 'border-line hover:border-line-hover'}"
							title={entry.label}
							aria-label={entry.label}
							on:click={() => pickFormImage(entry)}
						>
							<img src={entry.url} alt={entry.label} class="w-full h-full object-cover" />
							{#if isSelected(entry)}
								<span class="absolute top-0.5 right-0.5 w-3.5 h-3.5 rounded-full bg-signal text-accent-contrast grid place-items-center">
									<svg class="w-2 h-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
									</svg>
								</span>
							{/if}
						</button>
					{/each}
				</div>
			</div>
		{/if}

		<MediaLoaderField
			name="vision_image"
			value={selectedImageData}
			onChange={handleFieldChange}
			config={{ title: 'Reference image', accept: 'image/*' }}
			compact
			compactFullWidth
		/>

		{#if lastGeneratedImage}
			<div class="flex items-center gap-2.5 pt-3 border-t border-line">
				<img src={lastGeneratedImage.url} alt="" class="w-10 h-10 rounded-lg object-cover border border-line flex-shrink-0" />
				<div class="flex-1 min-w-0">
					<div class="text-xs font-medium text-fg">Use last generated image</div>
					<div class="text-2xs text-fg-subtle truncate mt-0.5">
						{lastGeneratedImage.name}{#if lastGeneratedImage.generatedAt} · {lastGeneratedImage.generatedAt}{/if}
					</div>
				</div>
				<button
					type="button"
					class="flex-shrink-0 h-7 px-2.5 rounded border border-line-strong bg-surface-2 text-xs font-medium text-fg hover:bg-surface-3 transition-colors duration-100"
					on:click={attachAgain}
				>
					Attach again
				</button>
			</div>
		{/if}
	</div>

	{#if onToggleAttachImage}
		<label class="flex items-center gap-2.5 px-3 py-2 border-t border-line bg-surface-2 text-2xs text-fg-muted cursor-pointer">
			<input
				type="checkbox"
				checked={alwaysAttachLastImage}
				on:change={() => onToggleAttachImage?.()}
				class="w-3.5 h-3.5 rounded border-line bg-canvas text-signal focus:ring-signal focus:ring-offset-0"
			/>
			<span class="flex-1">Auto-attach the last generated image to your next message</span>
			<span class="font-mono text-2xs text-fg-subtle uppercase tracking-[0.06em] flex-shrink-0">Once when new</span>
		</label>
	{/if}
</div>
